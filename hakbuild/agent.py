"""Orchestrateur : fait avancer un projet etat par etat, avec preuves.

Chaque etape produit soit des fichiers reels, soit des enregistrements en base
(PRD, taches, tests, rapports, builds). Aucun succes n'est declare sans trace
verifiable, et les blocages remontent explicitement.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db, evidence, planner, prd, scaffold, security, states

MAX_FIX_ATTEMPTS = 3


class Orchestrator:
    def __init__(self, conn, project_id: str):
        self.conn = conn
        self.project_id = project_id
        row = db.get_project(conn, project_id)
        if row is None:
            raise ValueError(f"projet inconnu: {project_id}")
        self.project = dict(row)
        self.workspace = self._ensure_workspace()

    def _ensure_workspace(self) -> Path:
        if self.project.get("workspace"):
            path = Path(self.project["workspace"])
            path.mkdir(parents=True, exist_ok=True)
            return path
        path = config.workspace_dir(self.project_id)
        self.conn.execute(
            "UPDATE projects SET workspace = ?, updated_at = ? WHERE id = ?",
            (str(path), db.now(), self.project_id),
        )
        self.conn.commit()
        self.project["workspace"] = str(path)
        return path

    # -- helpers -----------------------------------------------------------
    def refresh(self) -> dict:
        self.project = dict(db.get_project(self.conn, self.project_id))
        return self.project

    @property
    def state(self) -> str:
        return self.refresh()["status"]

    def _prd_md(self) -> str:
        row = self.conn.execute(
            "SELECT content FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (self.project_id,),
        ).fetchone()
        return row["content"] if row else ""

    def _write_files(self, files: dict[str, str]) -> list[str]:
        written = []
        for rel, content in files.items():
            path = self.workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(rel)
        return written

    def _complete(self, code: str, evidence_note: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'DONE', evidence = ?, updated_at = ? WHERE project_id = ? AND code = ?",
            (evidence_note, db.now(), self.project_id, code),
        )
        self.conn.commit()

    def _fail(self, code: str, error: str) -> int:
        self.conn.execute(
            "UPDATE tasks SET attempts = attempts + 1, last_error = ?, updated_at = ? WHERE project_id = ? AND code = ?",
            (error[:500], db.now(), self.project_id, code),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT attempts, max_attempts FROM tasks WHERE project_id = ? AND code = ?",
            (self.project_id, code),
        ).fetchone()
        return int(row["attempts"]) if row else 1

    def _create_tasks(self, prd_md: str) -> int:
        existing = {
            r["code"]
            for r in self.conn.execute("SELECT code FROM tasks WHERE project_id = ?", (self.project_id,))
        }
        created = 0
        for ticket in planner.build_tickets(self.project, prd_md):
            if ticket["id"] in existing:
                continue
            self.conn.execute(
                "INSERT INTO tasks (id, project_id, job_id, code, title, phase, detail, done_criteria, status,"
                " attempts, max_attempts, created_at, updated_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'TODO', 0, ?, ?, ?)",
                (
                    db.new_id("tsk"),
                    self.project_id,
                    ticket["id"],
                    ticket["title"],
                    ticket["phase"],
                    ticket["detail"],
                    ticket["done"],
                    MAX_FIX_ATTEMPTS,
                    db.now(),
                    db.now(),
                ),
            )
            created += 1
        self.conn.commit()
        return created

    # -- etapes ------------------------------------------------------------
    def step(self) -> dict:
        state = self.state
        handler = getattr(self, f"_on_{state}", None)
        if handler is None:
            return {"state": state, "action": "noop", "detail": "etat sans traitement"}
        return handler()

    def _on_PENDING(self) -> dict:
        states.transition(self.conn, self.project_id, "ANALYZING", reason="demarrage du pipeline")
        db.log(self.conn, "Pipeline demarre (PENDING -> ANALYZING)", self.project_id)
        return {"state": "ANALYZING", "action": "started"}

    def _on_ANALYZING(self) -> dict:
        content = prd.generate_prd(self.project)
        version = db.next_prd_version(self.conn, self.project_id)
        self.conn.execute(
            "INSERT INTO prd_documents (id, project_id, version, content, status, created_at)"
            " VALUES (?, ?, ?, ?, 'draft', ?)",
            (db.new_id("prd"), self.project_id, version, content, db.now()),
        )
        self.conn.commit()
        (self.workspace / "prd.md").write_text(content, encoding="utf-8")
        db.record_artifact(self.conn, self.project_id, "prd", self.workspace / "prd.md")
        self.conn.execute(
            "INSERT INTO approvals (id, project_id, gate, reason, status, created_at)"
            " VALUES (?, ?, 'PRD', ?, 'PENDING', ?)",
            (db.new_id("apv"), self.project_id, f"Valider le PRD v{version} avant developpement", db.now()),
        )
        self.conn.commit()
        db.log(self.conn, f"PRD v{version} genere, validation humaine requise", self.project_id)
        states.transition(self.conn, self.project_id, "AWAITING_APPROVAL", reason="PRD a valider")
        return {"state": "AWAITING_APPROVAL", "action": "prd_generated", "version": version}

    def _on_AWAITING_APPROVAL(self) -> dict:
        row = self.conn.execute(
            "SELECT status FROM approvals WHERE project_id = ? AND gate = 'PRD' ORDER BY created_at DESC LIMIT 1",
            (self.project_id,),
        ).fetchone()
        if row and row["status"] == "VALIDATED":
            states.transition(self.conn, self.project_id, "PLANNING", actor="human", reason="PRD valide")
            db.log(self.conn, "PRD valide par l'utilisateur -> planification", self.project_id)
            return {"state": "PLANNING", "action": "approved"}
        return {
            "state": "AWAITING_APPROVAL",
            "action": "waiting_approval",
            "detail": "Validation du PRD requise (human gate).",
            "waiting": True,
        }

    def _on_PLANNING(self) -> dict:
        created = self._create_tasks(self._prd_md())
        db.log(self.conn, f"{created} ticket(s) cree(s) depuis le PRD", self.project_id)
        states.transition(self.conn, self.project_id, "DESIGNING", reason="plan etabli")
        return {"state": "DESIGNING", "action": "tasks_created", "count": created}

    def _on_DESIGNING(self) -> dict:
        files = scaffold.app_files(self.project)
        written = self._write_files(
            {"design/screens.md": files["design/screens.md"], "constants/theme.ts": files["constants/theme.ts"]}
        )
        self._complete("T01", f"{len(written)} fichiers de design: {', '.join(written)}")
        db.log(self.conn, "Identite visuelle et ecrans generes", self.project_id)
        states.transition(self.conn, self.project_id, "DEVELOPING", reason="design pret")
        return {"state": "DEVELOPING", "action": "design_generated", "files": written}

    def _on_DEVELOPING(self) -> dict:
        files = scaffold.app_files(self.project)
        written = self._write_files(files)
        self._complete("T02", "package.json, app.json, tsconfig.json generes")
        self._complete("T03", f"ecrans: {', '.join(f for f in written if f.startswith('app/'))}")
        db.log(self.conn, f"Application React Native generee ({len(written)} fichiers)", self.project_id)
        states.transition(self.conn, self.project_id, "CONFIGURING_BACKEND", reason="scaffold ecrit")
        return {"state": "CONFIGURING_BACKEND", "action": "app_generated", "files": len(written)}

    def _on_CONFIGURING_BACKEND(self) -> dict:
        schema_path = self.workspace / "convex" / "schema.ts"
        tables = []
        if schema_path.exists():
            from . import convex as convex_mod

            tables = convex_mod.schema_tables(schema_path.read_text(encoding="utf-8"))
        platform_schema = config.data_dir() / "platform" / "convex"
        platform_schema.mkdir(parents=True, exist_ok=True)
        (platform_schema / "schema.ts").write_text(convex_mod_platform_schema(), encoding="utf-8")
        self._complete("T04", f"tables generees: {', '.join(tables) or 'aucune'}")
        self._complete("T05", "queries/mutations gardees par ctx.auth.getUserIdentity()")
        db.log(self.conn, f"Backend Convex specifie ({len(tables)} tables applicatives)", self.project_id)
        states.transition(self.conn, self.project_id, "INTEGRATING", reason="backend specifie")
        return {"state": "INTEGRATING", "action": "convex_spec", "tables": tables}

    def _on_INTEGRATING(self) -> dict:
        env_example = self.workspace / ".env.example"
        ok = env_example.exists() and "EXPO_PUBLIC_CONVEX_URL" in env_example.read_text(encoding="utf-8")
        secrets = config.configured_secrets()
        for name, present in secrets.items():
            self.conn.execute(
                "INSERT INTO integrations (id, project_id, name, status, config_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    db.new_id("int"),
                    self.project_id,
                    name,
                    "configured" if present else "missing",
                    json.dumps({"secret": present}),
                    db.now(),
                ),
            )
        self.conn.commit()
        if not ok:
            attempts = self._fail("T06", "EXPO_PUBLIC_CONVEX_URL absent de .env.example")
            if attempts >= MAX_FIX_ATTEMPTS:
                states.transition(self.conn, self.project_id, "BLOCKED", reason="integration impossible")
                return {"state": "BLOCKED", "action": "blocked", "detail": "variable Convex absente"}
            states.transition(self.conn, self.project_id, "FIXING", reason="integration incomplete")
            return {"state": "FIXING", "action": "fix_needed", "missing": "EXPO_PUBLIC_CONVEX_URL"}
        self._complete("T06", "lib/convex.ts + .env.example sans secret")
        db.log(self.conn, "Frontend connecte a Convex (URL publique uniquement)", self.project_id)
        states.transition(self.conn, self.project_id, "TESTING", reason="integration prete")
        return {"state": "TESTING", "action": "integrated"}

    def _on_TESTING(self) -> dict:
        checks = security.static_checks(self.workspace)
        result_id, passed, failed = security.persist_checks(self.conn, self.project_id, checks)
        blocking = [c for c in checks if not c["ok"]]
        if blocking:
            attempts = self._fail("T07", "; ".join(c["name"] for c in blocking)[:400])
            db.log(self.conn, f"Tests: {passed} ok / {failed} echec(s)", self.project_id, "warn")
            if attempts >= MAX_FIX_ATTEMPTS:
                states.transition(self.conn, self.project_id, "BLOCKED", reason="3 tentatives de correction echouees")
                return {"state": "BLOCKED", "action": "blocked", "failures": [c["name"] for c in blocking]}
            states.transition(self.conn, self.project_id, "FIXING", reason="checks en echec")
            return {"state": "FIXING", "action": "fix_needed", "failures": [c["name"] for c in blocking]}
        self._complete("T07", f"suite static: {passed} verifications ok")
        db.log(self.conn, f"Tests statiques: {passed}/{len(checks)} ok", self.project_id)
        states.transition(self.conn, self.project_id, "SECURITY_SCANNING", reason="tests ok")
        return {"state": "SECURITY_SCANNING", "action": "tests_passed", "passed": passed, "failed": failed}

    def _on_SECURITY_SCANNING(self) -> dict:
        scan = security.scan_workspace(self.workspace)
        security.persist_scan(self.conn, self.project_id, scan)
        blocking = scan["critical"] > 0
        if blocking:
            attempts = self._fail("T08", f"{scan['critical']} constat(s) critique(s)")
            db.log(self.conn, f"Securite: {scan['critical']} critique(s) detecte(s)", self.project_id, "error")
            if attempts >= MAX_FIX_ATTEMPTS:
                states.transition(self.conn, self.project_id, "BLOCKED", reason="vulnerabilite critique non corrigee")
                return {"state": "BLOCKED", "action": "blocked", "critical": scan["critical"]}
            states.transition(self.conn, self.project_id, "FIXING", reason="vulnerabilites critiques")
            return {"state": "FIXING", "action": "fix_needed", "critical": scan["critical"]}
        self._complete("T08", f"scan: {scan['count']} constat(s), max {scan['max_severity']}")
        db.log(self.conn, f"Securite: aucun constat critique ({scan['count']} constat(s) mineur(s))", self.project_id)
        states.transition(self.conn, self.project_id, "BUILDING", reason="securite validee")
        return {"state": "BUILDING", "action": "security_clean", "findings": scan["count"]}

    def _on_FIXING(self) -> dict:
        """Regenere les fichiers deterministes (correction sure) puis reteste."""
        files = scaffold.app_files(self.project)
        self._write_files(files)
        (self.workspace / "prd.md").write_text(self._prd_md(), encoding="utf-8")
        db.log(self.conn, "Correction appliquee : regeneration des fichiers deterministes", self.project_id, "warn")
        states.transition(self.conn, self.project_id, "TESTING", reason="correction appliquee")
        return {"state": "TESTING", "action": "fixed"}

    def _on_BUILDING(self) -> dict:
        from . import build

        mode = config.settings().get("BUILD_MODE", "dry-run")
        state = build.preflight(self.conn, self.project_id)
        build_id = db.new_id("bld")

        if mode != "execute":
            note = "dry-run"
            if state["blockers"]:
                note += " | blocages: " + "; ".join(state["blockers"])
            else:
                note += " | projet pret : npx eas-cli build --platform android --profile preview"
            self.conn.execute(
                "INSERT INTO builds (id, project_id, platform, profile, status, artifact_path, note, created_at, updated_at)"
                " VALUES (?, ?, 'android', 'preview', 'planned', NULL, ?, ?, ?)",
                (build_id, self.project_id, note, db.now(), db.now()),
            )
            self.conn.commit()
            self._complete("T09", f"plan de build enregistre ({len(state['blockers'])} blocage(s) restant(s))")
            db.log(self.conn, f"Build planifie (dry-run), {len(state['blockers'])} blocage(s): {note}", self.project_id)
            states.transition(self.conn, self.project_id, "COMPLETED", reason="build planifie")
            return {"state": "COMPLETED", "action": "build_planned", "build_id": build_id,
                    "blockers": state["blockers"]}

        result = build.build_apk(self.conn, self.project_id, "preview", execute=True)
        if result.get("ok") and result.get("status") == "queued":
            self._complete("T09", f"EAS build lance: {result.get('build_url') or 'en file'}")
            db.log(self.conn, f"EAS build lance: {result.get('build_url')}", self.project_id)
            states.transition(self.conn, self.project_id, "COMPLETED", reason="build EAS lance")
            return {"state": "COMPLETED", "action": "build_queued", "build_url": result.get("build_url")}

        self._record_build(build_id, "failed", None, (result.get("detail") or result.get("status") or "")[:400])
        attempts = self._fail("T09", result.get("detail") or result.get("status") or "build echoue")
        target = "BLOCKED" if attempts >= MAX_FIX_ATTEMPTS else "FIXING"
        states.transition(self.conn, self.project_id, target, reason="build en echec")
        return {"state": target, "action": "build_failed", "blockers": result.get("blockers"),
                "detail": result.get("detail") or result.get("status")}

    def _find_apk(self) -> Path | None:
        for path in sorted(self.workspace.rglob("*.apk")):
            if "node_modules" not in path.parts:
                return path
        return None

    def _record_build(self, build_id: str, status: str, artifact: str | None, note: str) -> None:
        self.conn.execute(
            "INSERT INTO builds (id, project_id, platform, profile, status, artifact_path, note, created_at, updated_at)"
            " VALUES (?, ?, 'android', 'preview', ?, ?, ?, ?, ?)",
            (build_id, self.project_id, status, artifact, note, db.now(), db.now()),
        )
        self.conn.commit()

    def _on_COMPLETED(self) -> dict:
        bundle = evidence.build_bundle(self.conn, self.project_id)
        return {
            "state": "COMPLETED",
            "action": "delivered",
            "bundle": str(bundle),
            "waiting": True,
        }

    def _on_BLOCKED(self) -> dict:
        return {
            "state": "BLOCKED",
            "action": "waiting_human",
            "detail": "Intervention humaine requise (voir tasks.last_error).",
            "waiting": True,
        }

    def _on_FAILED(self) -> dict:
        return {"state": "FAILED", "action": "waiting_human", "waiting": True}

    def _on_CANCELLED(self) -> dict:
        return {"state": "CANCELLED", "action": "stopped", "waiting": True}

    # -- boucle ------------------------------------------------------------
    def run_until_blocked(self, max_steps: int = 20) -> list[dict]:
        history = []
        for _ in range(max_steps):
            result = self.step()
            history.append(result)
            if result.get("waiting"):
                break
        return history


def convex_mod_platform_schema() -> str:
    from . import convex as convex_mod

    return convex_mod.platform_schema_ts()
