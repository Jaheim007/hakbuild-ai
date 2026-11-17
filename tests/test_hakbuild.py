"""Tests stdlib de HakBuild AI (unittest, aucun pip)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

# Cle factice construite a l'execution : un litteral would trip secret scanners
FAKE_KEY = "sk-or-" + "v1-" + "x" * 24

from hakbuild import agent, cli, config, convex, db, evidence, planner, prd, scaffold, security, server, states


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["DATA_DIR"] = str(root / "data")
        os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'test.db').as_posix()}"
        # isoler les tests du .env reel (aucun secret ne doit fuiter dans les assertions)
        self._env_file = config.ENV_FILE
        config.ENV_FILE = root / ".env"
        config.ENV_FILE.write_text("# test env\n", encoding="utf-8")
        for key in ("EXPO_TOKEN", "EAS_TOKEN", "OPENROUTER_API_KEY", "GITHUB_TOKEN", "CONVEX_DEPLOY_KEY"):
            os.environ.pop(key, None)
        self.conn = db.connect()
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        config.ENV_FILE = self._env_file
        self.tmp.cleanup()
        os.environ.pop("DATA_DIR", None)
        os.environ.pop("DATABASE_URL", None)


class TestFoundation(Base):
    def test_migrations_idempotent(self):
        self.assertEqual(db.migrate(self.conn), 0)
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("projects", "tasks", "approvals", "builds", "security_reports", "audit_logs"):
            self.assertIn(required, tables)

    def test_create_project_gives_credits(self):
        project_id = cli.create_project(self.conn, "Test App", "Une idee de test", "android")
        project = db.get_project(self.conn, project_id)
        self.assertEqual(project["status"], "PENDING")
        self.assertTrue(Path(project["workspace"]).exists())
        user = self.conn.execute("SELECT credits FROM users WHERE id = ?", (project["owner_id"],)).fetchone()
        self.assertEqual(user["credits"], 50)


class TestStateMachine(Base):
    def test_valid_and_invalid_transitions(self):
        project_id = cli.create_project(self.conn, "S", "idee", "android")
        self.assertTrue(states.can_transition("PENDING", "ANALYZING"))
        self.assertFalse(states.can_transition("PENDING", "COMPLETED"))
        states.transition(self.conn, project_id, "ANALYZING", reason="test")
        with self.assertRaises(states.InvalidTransition):
            states.transition(self.conn, project_id, "BUILDING")
        audit = self.conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='project.transition'").fetchone()[0]
        self.assertGreaterEqual(audit, 1)


class TestPrdAndPlan(Base):
    def test_prd_sections_and_domain(self):
        project = {"name": "Barber", "idea": "Reservation de creneaux pour barbiers avec paiement mobile money", "platforms": "both"}
        content = prd.generate_prd(project)
        for section in ("## 2. Souffrance", "## 5. Fonctionnalites", "## 8. Besoins en base de donnees", "## 9. Criteres de reussite"):
            self.assertIn(section, content)
        self.assertIn("reservations", content.lower())
        self.assertTrue(prd.acceptance_criteria(content))

    def test_features_from_bullets(self):
        features = prd.extract_features("App - choix du creneau - paiement - notification")
        joined = " ".join(features).lower()
        self.assertIn("paiement", joined)
        self.assertIn("creneau", joined)

    def test_tickets_cover_phases(self):
        tickets = planner.build_tickets({"name": "X", "idea": "paiement"}, "# PRD\n")
        phases = {t["phase"] for t in tickets}
        for phase in ("DESIGNING", "DEVELOPING", "CONFIGURING_BACKEND", "INTEGRATING", "TESTING", "SECURITY_SCANNING", "BUILDING"):
            self.assertIn(phase, phases)
        self.assertTrue(all(t["done"] for t in tickets))


class TestGenerators(Base):
    def test_convex_schema_and_guards(self):
        schema = convex.app_schema_ts([("users", "email, displayName"), ("bookings", "date, status")])
        self.assertIn('defineTable', schema)
        self.assertIn("bookings", schema)
        fn = convex.app_functions_ts("bookings", "date, status")
        self.assertIn("ctx.auth.getUserIdentity()", fn)
        self.assertIn("ownerId", fn)
        platform = convex.platform_schema_ts()
        tables = convex.schema_tables(platform)
        for expected in ("projects", "tasks", "builds", "securityReports", "approvals", "creditTransactions"):
            self.assertIn(expected, tables)
        self.assertEqual(len(tables), 21)

    def test_scaffold_files_valid_json(self):
        files = scaffold.app_files({"name": "Barber Book", "idea": "reservation et paiement", "platforms": "both"})
        pkg = json.loads(files["package.json"])
        self.assertIn("expo", pkg["dependencies"])
        app_cfg = json.loads(files["app.json"])
        self.assertEqual(app_cfg["expo"]["slug"], "barber-book")
        self.assertIn("export default", files["app/index.tsx"])
        self.assertIn("EXPO_PUBLIC_CONVEX_URL", files["lib/convex.ts"])
        self.assertNotIn("sk-", files["lib/convex.ts"])
        # bundling sans deployment Convex : anyApi + babel requis
        self.assertIn("anyApi", files["lib/api.ts"])
        self.assertIn("babel-preset-expo", files["babel.config.js"])
        self.assertNotIn("_generated", files["app/index.tsx"])


class TestSecurity(Base):
    def test_detects_secret_and_unguarded_function(self):
        root = Path(self.tmp.name) / "ws"
        (root / "app").mkdir(parents=True)
        (root / "convex").mkdir()
        (root / "app" / "leak.ts").write_text(f'const key = "{FAKE_KEY}";\n')
        (root / "convex" / "open.ts").write_text(
            'import { query } from "./_generated/server";\nexport const list = query({ args: {}, handler: async (ctx) => [] });\n'
        )
        scan = security.scan_workspace(root)
        rules = {f["rule"] for f in scan["findings"]}
        self.assertIn("openrouter_key", rules)
        self.assertIn("unguarded_function", rules)
        self.assertEqual(scan["max_severity"], "critical")

    def test_clean_scaffold_passes_checks(self):
        root = Path(self.tmp.name) / "app_ws"
        files = scaffold.app_files({"name": "Clean", "idea": "stock de produits", "platforms": "android"})
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        checks = security.static_checks(root)
        failed = [c for c in checks if not c["ok"]]
        self.assertEqual(failed, [], f"checks en echec: {failed}")
        scan = security.scan_workspace(root)
        self.assertEqual(scan["critical"], 0)


class TestPipeline(Base):
    def _project(self):
        return cli.create_project(self.conn, "BarberBook", "reservation avec paiement mobile money", "both")

    def test_full_pipeline_to_delivery(self):
        project_id = self._project()
        orch = agent.Orchestrator(self.conn, project_id)
        history = orch.run_until_blocked(max_steps=5)
        self.assertEqual(orch.state, "AWAITING_APPROVAL")
        self.assertTrue(history[-1]["waiting"])

        cli.cmd_approve(type("A", (), {"id": project_id, "by": "test"})())
        history = orch.run_until_blocked(max_steps=20)
        self.assertEqual(orch.state, "COMPLETED")
        actions = [h["action"] for h in history]
        self.assertIn("build_planned", actions)
        self.assertIn("delivered", actions)

        tasks = self.conn.execute("SELECT status FROM tasks WHERE project_id = ?", (project_id,)).fetchall()
        self.assertTrue(all(t["status"] == "DONE" for t in tasks), [t["status"] for t in tasks])

        bundle = Path(history[-1]["bundle"])
        self.assertTrue(bundle.exists())
        with zipfile.ZipFile(bundle) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
        self.assertIn("prd.md", names)
        self.assertIn("reports/README_REPORT.md", names)
        self.assertTrue(any(n.startswith("source/app/") for n in names))
        self.assertGreater(bundle.stat().st_size, 4000)

    def test_blocked_after_three_failed_fixes(self):
        project_id = self._project()
        orch = agent.Orchestrator(self.conn, project_id)
        orch.run_until_blocked(max_steps=5)
        cli.cmd_approve(type("A", (), {"id": project_id, "by": "test"})())
        orch.run_until_blocked(max_steps=20)
        # on force un secret critique dans le workspace puis on relance l'analyse
        leak = Path(db.get_project(self.conn, project_id)["workspace"]) / "app" / "leak.ts"
        leak.write_text(f'export const k = "{FAKE_KEY}";\n')
        self.conn.execute("UPDATE projects SET status='SECURITY_SCANNING' WHERE id=?", (project_id,))
        self.conn.commit()
        orch.run_until_blocked(max_steps=10)
        status = db.get_project(self.conn, project_id)["status"]
        self.assertIn(status, {"FIXING", "BLOCKED", "TESTING"})
        for _ in range(4):
            if status == "BLOCKED":
                break
            leak.write_text(f'export const k = "{FAKE_KEY}";\n')
            self.conn.execute("UPDATE projects SET status='SECURITY_SCANNING' WHERE id=?", (project_id,))
            self.conn.commit()
            orch.run_until_blocked(max_steps=10)
            status = db.get_project(self.conn, project_id)["status"]
        self.assertEqual(status, "BLOCKED")


class TestVerify(Base):
    def test_missing_deps_is_reported_honestly(self):
        from hakbuild import verify

        project_id = cli.create_project(self.conn, "Verif", "idee simple", "android")
        workspace = Path(db.get_project(self.conn, project_id)["workspace"])
        (workspace / "package.json").write_text('{"name":"verif"}', encoding="utf-8")
        result = verify.verify_web(self.conn, project_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("status"), "missing_deps")

    def test_verify_without_package_json(self):
        from hakbuild import verify

        project_id = cli.create_project(self.conn, "Vide", "idee", "android")
        (Path(db.get_project(self.conn, project_id)["workspace"]) / "package.json").unlink(missing_ok=True)
        result = verify.verify_web(self.conn, project_id)
        self.assertFalse(result["ok"])
        self.assertIn("DEVELOPING", result["error"])


class TestBuild(Base):
    def test_eas_json_valid(self):
        files = scaffold.app_files({"name": "Build Test", "idea": "reservation", "platforms": "android"})
        eas = json.loads(files["eas.json"])
        self.assertEqual(eas["build"]["preview"]["android"]["buildType"], "apk")
        self.assertEqual(eas["build"]["production"]["android"]["buildType"], "app-bundle")
        app_cfg = json.loads(files["app.json"])
        self.assertEqual(app_cfg["expo"]["android"]["versionCode"], 1)

    def test_preflight_lists_blockers(self):
        from hakbuild import build

        project_id = cli.create_project(self.conn, "Preflight", "reservation", "android")
        workspace = Path(db.get_project(self.conn, project_id)["workspace"])
        for rel, content in scaffold.app_files(dict(db.get_project(self.conn, project_id))).items():
            path = workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        state = build.preflight(self.conn, project_id)
        self.assertFalse(state["ready"])
        joined = " ".join(state["blockers"]).lower()
        self.assertIn("npm install", joined)
        self.assertIn("token", joined)
        self.assertIn("eas-cli init", joined)
        keys = {c["key"] for c in state["checks"]}
        self.assertEqual(len(keys), 8)
        self.assertIn("eas_json", keys)
        self.assertIn("expo_token", keys)

    def test_token_is_detected_without_leaking(self):
        from hakbuild import build

        os.environ["EXPO_TOKEN"] = "expo-test-token-value"
        try:
            self.assertTrue(build.token())
            self.assertEqual(build.env_with_token()["EXPO_TOKEN"], "expo-test-token-value")
        finally:
            os.environ.pop("EXPO_TOKEN", None)

    def test_build_without_token_is_blocked_honestly(self):
        from hakbuild import build

        os.environ.pop("EXPO_TOKEN", None)
        os.environ.pop("EAS_TOKEN", None)
        project_id = cli.create_project(self.conn, "NoToken", "reservation", "android")
        result = build.build_apk(self.conn, project_id, execute=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["blockers"])

    def test_eas_env_bypasses_windows_git_clone(self):
        from hakbuild import build

        os.environ.pop("HAKBUILD_EAS_NO_VCS", None)
        self.assertEqual(build.eas_env().get("EAS_NO_VCS"), "1")
        os.environ["HAKBUILD_EAS_NO_VCS"] = "0"
        try:
            self.assertNotIn("EAS_NO_VCS", build.eas_env())
        finally:
            os.environ.pop("HAKBUILD_EAS_NO_VCS", None)

    def test_easignore_excludes_heavy_dirs(self):
        files = scaffold.app_files({"name": "Ignore", "idea": "reservation", "platforms": "android"})
        ignore = files[".easignore"]
        for pattern in ("node_modules/", "dist/", ".git/", ".env"):
            self.assertIn(pattern, ignore)


class TestGithub(Base):
    def test_repo_name_slug(self):
        from hakbuild import github

        self.assertEqual(github.repo_name({"name": "Barber Pro!"}), "barber-pro")
        self.assertEqual(github.repo_name({"name": "  "}), "app")

    def test_gh_discovery_and_env(self):
        from hakbuild import github

        directory = github.gh_dir()
        self.assertIsNotNone(directory, "gh introuvable (attendu: ~/gh-portable/bin/gh.exe)")
        self.assertIn("PATH", github.gh_env())

    def test_push_unknown_project(self):
        from hakbuild import github

        result = github.push_project(self.conn, "prj_inconnu")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "projet inconnu")


class TestServer(Base):
    def test_api_endpoints(self):
        httpd = server.make_server(self.conn, "127.0.0.1", 0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/api/health") as resp:
                self.assertTrue(json.loads(resp.read())["ok"])
            with urllib.request.urlopen(base + "/") as resp:
                self.assertIn(b"HakBuild", resp.read())

            payload = json.dumps({"name": "Web App", "idea": "livraison de colis avec suivi", "platforms": "android"}).encode()
            req = urllib.request.Request(base + "/api/projects", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                project_id = json.loads(resp.read())["id"]
            self.assertTrue(project_id.startswith("prj_"))

            with urllib.request.urlopen(base + "/api/project?id=" + project_id) as resp:
                data = json.loads(resp.read())
            self.assertEqual(data["project"]["status"], "PENDING")

            payload = json.dumps({"id": project_id, "message": "avancer"}).encode()
            req = urllib.request.Request(base + "/api/chat", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                chat = json.loads(resp.read())
            self.assertIn("Pipeline", chat["reply"])
            self.assertEqual(chat["snapshot"]["project"]["status"], "AWAITING_APPROVAL")

            payload = json.dumps({"id": project_id}).encode()
            req = urllib.request.Request(base + "/api/approve", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())
            self.assertEqual(result["project"]["project"]["status"], "COMPLETED")

            with urllib.request.urlopen(base + "/api/download?id=" + project_id + "&kind=bundle") as resp:
                body = resp.read()
            self.assertEqual(body[:2], b"PK")
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
