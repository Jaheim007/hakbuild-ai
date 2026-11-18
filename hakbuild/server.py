"""Dashboard web HakBuild AI (http.server, stdlib uniquement).

API JSON + page statique. Ecoute en local par defaut. Toute action sensible
(validation PRD, avancement, build) passe par une route explicite journalisee.
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, config, db, evidence, runner, states
from .cli import create_project, ensure_user

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCK = threading.Lock()


def _ro(conn, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


def snapshot(conn, project_id: str) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"error": "projet inconnu"}
    project = dict(project)
    return {
        "project": project,
        "label": states.LABELS.get(project["status"], project["status"]),
        "prd": _ro(conn, "SELECT id, version, status, content, created_at FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)),
        "tasks": _ro(conn, "SELECT code, title, phase, status, attempts, evidence, last_error FROM tasks WHERE project_id = ? ORDER BY code", (project_id,)),
        "logs": _ro(conn, "SELECT level, source, message, created_at FROM logs WHERE project_id = ? ORDER BY id DESC LIMIT 80", (project_id,)),
        "builds": _ro(conn, "SELECT platform, profile, status, artifact_path, note, created_at FROM builds WHERE project_id = ? ORDER BY created_at DESC", (project_id,)),
        "tests": _ro(conn, "SELECT suite, passed, failed, created_at FROM test_results WHERE project_id = ? ORDER BY created_at DESC LIMIT 3", (project_id,)),
        "security": _ro(conn, "SELECT severity, findings_json, created_at FROM security_reports WHERE project_id = ? ORDER BY created_at DESC LIMIT 3", (project_id,)),
        "approvals": _ro(conn, "SELECT gate, status, reason, created_at FROM approvals WHERE project_id = ? ORDER BY created_at DESC", (project_id,)),
        "runs": _ro(conn, "SELECT command, exit_code, duration_ms FROM agent_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT 10", (project_id,)),
        "messages": _ro(conn, "SELECT role, content, created_at FROM messages WHERE project_id = ? ORDER BY created_at ASC LIMIT 200", (project_id,)),
        "artifacts": _ro(conn, "SELECT kind, path, size, sha256 FROM artifacts WHERE project_id = ? ORDER BY created_at DESC", (project_id,)),
    }


def _conversation(conn, project_id: str) -> str:
    row = conn.execute("SELECT id FROM conversations WHERE project_id = ? LIMIT 1", (project_id,)).fetchone()
    if row:
        return row["id"]
    conv_id = db.new_id("cnv")
    conn.execute(
        "INSERT INTO conversations (id, project_id, title, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, project_id, "Conversation projet", db.now()),
    )
    conn.commit()
    return conv_id


def _say(conn, project_id: str, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO messages (id, conversation_id, project_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (db.new_id("msg"), _conversation(conn, project_id), project_id, role, content, db.now()),
    )
    conn.commit()


def chat(conn, project_id: str, message: str) -> dict:
    text = (message or "").strip()
    if not text:
        return {"reply": "Message vide."}
    _say(conn, project_id, "user", text)
    low = text.lower()
    project = dict(db.get_project(conn, project_id))
    reply = ""

    if any(word in low for word in ("avance", "continue", "next", "go", "lance")):
        result = agent.Orchestrator(conn, project_id).run_until_blocked(max_steps=12)
        reply = "Pipeline execute :\n" + "\n".join(
            f"- {entry.get('action')} -> {entry.get('state')}" for entry in result
        )
    elif "prd" in low:
        row = conn.execute(
            "SELECT content FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)
        ).fetchone()
        reply = row["content"] if row else "Aucun PRD genere. Envoyez 'avancer'."
    elif any(word in low for word in ("statut", "status", "etat", "état", "ou en est", "où en est")):
        reply = f"Projet {project['name']} : {project['status']} ({states.LABELS.get(project['status'], '')})."
    elif any(word in low for word in ("build", "compil", "apk")):
        reply = (
            "Build : mode actuel `" + str(config.settings().get("BUILD_MODE"))
            + "`. Pour compiler reellement, definir BUILD_MODE=execute, renseigner EAS_TOKEN, puis avancer."
        )
    elif any(word in low for word in ("tache", "tâche", "ticket", "plan")):
        rows = conn.execute(
            "SELECT code, title, status FROM tasks WHERE project_id = ? ORDER BY code", (project_id,)
        ).fetchall()
        reply = "\n".join(f"- {r['code']} [{r['status']}] {r['title']}" for r in rows) or "Aucune tache."
    elif any(word in low for word in ("securite", "sécurité", "security")):
        row = conn.execute(
            "SELECT severity, findings_json FROM security_reports WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        reply = f"Severite max : {row['severity']}, constats : {len(json.loads(row['findings_json']))}" if row else "Aucun scan encore execute."
    else:
        reply = (
            "Commandes disponibles : 'avancer', 'statut', 'prd', 'tickets', 'securite', 'build'. "
            f"Etat actuel : {project['status']}."
        )

    _say(conn, project_id, "agent", reply)
    return {"reply": reply, "snapshot": snapshot(conn, project_id)}


class Handler(BaseHTTPRequestHandler):
    server_version = "HakBuild/0.1"
    conn = None

    def log_message(self, fmt, *args):  # silencieux
        pass

    # -- helpers -----------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, download_name: str | None = None):
        if not path.exists():
            return self._send_json({"error": f"fichier absent: {path.name}"}, 404)
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._send_file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if ".." in rel:
                return self._send_json({"error": "chemin invalide"}, 400)
            return self._send_file(STATIC_DIR / rel)

        if path == "/api/health":
            return self._send_json({"ok": True, "db": str(config.db_path()), "build_mode": config.settings().get("BUILD_MODE")})

        if path == "/api/init":
            applied = db.migrate(self.conn)
            return self._send_json({"migrations_applied": applied, "ok": True})

        if path == "/api/state":
            counts = _ro(self.conn, "SELECT status, COUNT(*) AS n FROM projects GROUP BY status")
            return self._send_json({
                "projects": _ro(self.conn, "SELECT COUNT(*) AS n FROM projects")[0]["n"],
                "by_status": counts,
                "toolchain": runner.toolchain_report(),
                "secrets": config.configured_secrets(),
                "build_mode": config.settings().get("BUILD_MODE"),
                "states": states.STATES,
            })

        if path == "/api/projects":
            return self._send_json(_ro(
                self.conn,
                "SELECT id, name, status, platforms, created_at FROM projects ORDER BY created_at DESC",
            ))

        if path == "/api/project":
            project_id = (query.get("id") or [""])[0]
            with LOCK:
                return self._send_json(snapshot(self.conn, project_id))

        if path == "/api/download":
            project_id = (query.get("id") or [""])[0]
            kind = (query.get("kind") or ["bundle"])[0]
            project = db.get_project(self.conn, project_id)
            if project is None:
                return self._send_json({"error": "projet inconnu"}, 404)
            if kind == "prd":
                path = Path(project["workspace"]) / "prd.md"
                return self._send_file(path, f"{project_id}-prd.md")
            row = self.conn.execute(
                "SELECT path FROM artifacts WHERE project_id = ? AND kind = 'bundle' ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if row is None:
                return self._send_json({"error": "aucun bundle genere"}, 404)
            return self._send_file(Path(row["path"]), Path(row["path"]).name)

        return self._send_json({"error": "route inconnue"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._body()
        try:
            with LOCK:
                if parsed.path == "/api/projects":
                    name = (data.get("name") or "").strip()
                    idea = (data.get("idea") or "").strip()
                    if not name or not idea:
                        return self._send_json({"error": "nom et idee obligatoires"}, 400)
                    project_id = create_project(
                        self.conn, name, idea, data.get("platforms") or "android",
                        data.get("owner") or "owner@hakbuild.local",
                    )
                    return self._send_json({"id": project_id, "project": snapshot(self.conn, project_id)})

                if parsed.path == "/api/advance":
                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    history = agent.Orchestrator(self.conn, project_id).run_until_blocked(
                        max_steps=int(data.get("steps") or 12)
                    )
                    return self._send_json({"history": history, "project": snapshot(self.conn, project_id)})

                if parsed.path == "/api/approve":
                    project_id = data.get("id") or ""
                    row = self.conn.execute(
                        "SELECT id FROM approvals WHERE project_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
                        (project_id,),
                    ).fetchone()
                    if row is None:
                        return self._send_json({"error": "aucune validation en attente"}, 400)
                    self.conn.execute(
                        "UPDATE approvals SET status = 'VALIDATED', decided_at = ?, decided_by = ? WHERE id = ?",
                        (db.now(), data.get("by") or "owner", row["id"]),
                    )
                    self.conn.execute(
                        "UPDATE prd_documents SET status = 'validated', validated_at = ? WHERE project_id = ? AND status = 'draft'",
                        (db.now(), project_id),
                    )
                    self.conn.commit()
                    db.audit(self.conn, data.get("by") or "owner", "approval.validate", project_id)
                    orch = agent.Orchestrator(self.conn, project_id)
                    history = orch.run_until_blocked(max_steps=int(data.get("steps") or 12))
                    return self._send_json({"history": history, "project": snapshot(self.conn, project_id)})

                if parsed.path == "/api/chat":
                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    return self._send_json(chat(self.conn, project_id, data.get("message") or ""))

                if parsed.path == "/api/bundle":
                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    path = evidence.build_bundle(self.conn, project_id)
                    return self._send_json({"bundle": str(path), "size": path.stat().st_size})

                if parsed.path == "/api/verify":
                    from . import verify

                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    result = verify.verify_web(self.conn, project_id, install=bool(data.get("install")))
                    return self._send_json(result)

                if parsed.path == "/api/preflight":
                    from . import build

                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    return self._send_json(build.preflight(self.conn, project_id))

                if parsed.path == "/api/apk":
                    from . import build

                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    result = build.build_apk(
                        self.conn, project_id,
                        profile=data.get("profile") or "preview",
                        execute=bool(data.get("execute")),
                        wait=bool(data.get("wait")),
                    )
                    return self._send_json(result)

                if parsed.path == "/api/build-status":
                    from . import build

                    project_id = data.get("id") or ""
                    return self._send_json(build.refresh_build(self.conn, project_id))

                if parsed.path == "/api/github":
                    from . import github

                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    result = github.push_project(
                        self.conn, project_id,
                        name=data.get("name") or None,
                        private=bool(data.get("private", True)),
                    )
                    return self._send_json(result)

                if parsed.path == "/api/convex":
                    from . import convex_deploy

                    project_id = data.get("id") or ""
                    if db.get_project(self.conn, project_id) is None:
                        return self._send_json({"error": "projet inconnu"}, 404)
                    if data.get("status"):
                        return self._send_json(convex_deploy.status(self.conn, project_id))
                    return self._send_json(convex_deploy.deploy(self.conn, project_id))
        except Exception as exc:  # noqa: BLE001 - remonte au client sans secret
            return self._send_json({"error": str(exc), "type": type(exc).__name__}, 500)

        return self._send_json({"error": "route inconnue"}, 404)


def make_server(conn, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    Handler.conn = conn
    ensure_user(conn, "owner@hakbuild.local")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve(conn, host: str = "127.0.0.1", port: int = 8080) -> int:
    db.migrate(conn)
    httpd = make_server(conn, host, port)
    print(f"HakBuild AI -> http://{host}:{port}")
    print(f"Base         -> {config.db_path()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        httpd.server_close()
    return 0
