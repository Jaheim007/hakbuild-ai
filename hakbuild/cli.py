"""Interface CLI de HakBuild AI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agent, config, db, evidence, planner, prd as prd_mod, security, states


def _conn() -> "object":
    conn = db.connect()
    db.migrate(conn)
    return conn


def ensure_user(conn, email: str = "owner@hakbuild.local", name: str = "Owner") -> str:
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        return row["id"]
    user_id = db.new_id("usr")
    conn.execute(
        "INSERT INTO users (id, email, name, plan, credits, created_at) VALUES (?, ?, ?, 'creator', 50, ?)",
        (user_id, email, name, db.now()),
    )
    conn.execute(
        "INSERT INTO credit_transactions (id, user_id, delta, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (db.new_id("crt"), user_id, 50, "welcome", db.now()),
    )
    conn.commit()
    return user_id


def create_project(conn, name: str, idea: str, platforms: str = "android", owner_email: str = "owner@hakbuild.local") -> str:
    owner_id = ensure_user(conn, owner_email)
    project_id = db.new_id("prj")
    workspace = config.workspace_dir(project_id)
    conn.execute(
        "INSERT INTO projects (id, owner_id, name, idea, platforms, status, workspace, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)",
        (project_id, owner_id, name, idea, platforms, str(workspace), db.now(), db.now()),
    )
    conn.commit()
    db.audit(conn, "cli", "project.create", project_id, {"name": name, "platforms": platforms})
    db.log(conn, f"Projet cree: {name}", project_id)
    return project_id


def cmd_init_db(args) -> int:
    conn = db.connect()
    applied = db.migrate(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"Base: {config.db_path()}")
    print(f"Migrations appliquees: {applied} (total {len(db.MIGRATIONS)})")
    print(f"Tables ({len(tables)}): {', '.join(tables)}")
    return 0


def cmd_new(args) -> int:
    conn = _conn()
    project_id = create_project(conn, args.name, args.idea, args.platforms)
    print(project_id)
    return 0


def cmd_list(args) -> int:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, name, status, platforms, created_at FROM projects ORDER BY created_at DESC"
    ).fetchall()
    if not rows:
        print("Aucun projet.")
        return 0
    for row in rows:
        print(f"{row['id']}  {row['status']:<20} {row['name']}  [{row['platforms']}]  {row['created_at']}")
    return 0


def cmd_show(args) -> int:
    conn = _conn()
    project = db.get_project(conn, args.id)
    if project is None:
        print(f"Projet inconnu: {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "id": project["id"],
            "name": project["name"],
            "status": project["status"],
            "label": states.LABELS.get(project["status"], project["status"]),
            "platforms": project["platforms"],
            "workspace": project["workspace"],
            "credits_spent": project["credits_spent"],
        },
        indent=2, ensure_ascii=False,
    ))
    tasks = conn.execute(
        "SELECT code, title, status, attempts, evidence FROM tasks WHERE project_id = ? ORDER BY code", (args.id,)
    ).fetchall()
    for task in tasks:
        print(f"  {task['code']} {task['status']:<6} {task['title']}")
    return 0


def cmd_advance(args) -> int:
    conn = _conn()
    orch = agent.Orchestrator(conn, args.id)
    history = orch.run_until_blocked(max_steps=args.steps)
    for entry in history:
        print(json.dumps(entry, ensure_ascii=False))
    print(f"--- etat final: {orch.state} ({states.LABELS.get(orch.state, '')})")
    return 0


def cmd_approve(args) -> int:
    conn = _conn()
    row = conn.execute(
        "SELECT id FROM approvals WHERE project_id = ? AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (args.id,),
    ).fetchone()
    if row is None:
        print("Aucune validation en attente.", file=sys.stderr)
        return 1
    conn.execute(
        "UPDATE approvals SET status = 'VALIDATED', decided_at = ?, decided_by = ? WHERE id = ?",
        (db.now(), args.by, row["id"]),
    )
    conn.execute(
        "UPDATE prd_documents SET status = 'validated', validated_at = ? WHERE project_id = ? AND status = 'draft'",
        (db.now(), args.id),
    )
    conn.commit()
    db.audit(conn, args.by, "approval.validate", args.id)
    print(f"PRD valide pour {args.id}")
    return 0


def cmd_prd(args) -> int:
    conn = _conn()
    project = db.get_project(conn, args.id)
    if project is None:
        print("Projet inconnu", file=sys.stderr)
        return 1
    if args.print:
        row = conn.execute(
            "SELECT content FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1", (args.id,)
        ).fetchone()
        print(row["content"] if row else prd_mod.generate_prd(dict(project)))
    return 0


def cmd_scan(args) -> int:
    conn = _conn()
    project = db.get_project(conn, args.id)
    if project is None:
        print("Projet inconnu", file=sys.stderr)
        return 1
    scan = security.scan_workspace(Path(project["workspace"]))
    checks = security.static_checks(Path(project["workspace"]))
    print(json.dumps({"security": scan, "checks": checks}, indent=2, ensure_ascii=False))
    return 0


def cmd_build(args) -> int:
    conn = _conn()
    bundle = evidence.build_bundle(conn, args.id)
    print(f"Bundle: {bundle}")
    print(f"Taille: {bundle.stat().st_size} octets")
    return 0


def cmd_verify(args) -> int:
    from . import verify

    conn = _conn()
    result = verify.verify_web(conn, args.id, install=args.install)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_preflight(args) -> int:
    from . import build

    conn = _conn()
    state = build.preflight(conn, args.id)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["ready"] else 1


def cmd_apk(args) -> int:
    from . import build

    conn = _conn()
    result = build.build_apk(conn, args.id, profile=args.profile, execute=args.execute, wait=args.wait)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_build_status(args) -> int:
    from . import build

    conn = _conn()
    result = build.refresh_build(conn, args.id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_tickets(args) -> int:
    conn = _conn()
    project = db.get_project(conn, args.id)
    if project is None:
        print("Projet inconnu", file=sys.stderr)
        return 1
    row = conn.execute(
        "SELECT content FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1", (args.id,)
    ).fetchone()
    content = row["content"] if row else prd_mod.generate_prd(dict(project))
    for ticket in planner.build_tickets(dict(project), content):
        print(f"{ticket['id']} [{ticket['phase']}] {ticket['title']}\n    fait: {ticket['done']}")
    return 0


def cmd_serve(args) -> int:
    from . import server

    conn = _conn()
    return server.serve(conn, host=args.host, port=args.port)


def cmd_status(args) -> int:
    from .runner import toolchain_report

    print(json.dumps(
        {
            "database": str(config.db_path()),
            "secrets": config.configured_secrets(),
            "toolchain": toolchain_report(),
            "build_mode": config.settings().get("BUILD_MODE"),
        },
        indent=2,
    ))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hakbuild", description="HakBuild AI - orchestrateur MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="applique les migrations").set_defaults(func=cmd_init_db)

    p = sub.add_parser("new", help="cree un projet")
    p.add_argument("name")
    p.add_argument("idea")
    p.add_argument("--platforms", default="android", choices=["android", "ios", "both"])
    p.set_defaults(func=cmd_new)

    sub.add_parser("list", help="liste les projets").set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="detail d'un projet")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("advance", help="fait avancer le pipeline")
    p.add_argument("id")
    p.add_argument("--steps", type=int, default=20)
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("approve", help="valide le PRD (human gate)")
    p.add_argument("id")
    p.add_argument("--by", default="owner")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("prd", help="affiche le PRD")
    p.add_argument("id")
    p.add_argument("--print", action="store_true", default=True)
    p.set_defaults(func=cmd_prd)

    p = sub.add_parser("tickets", help="liste les tickets du plan")
    p.add_argument("id")
    p.set_defaults(func=cmd_tickets)

    p = sub.add_parser("scan", help="scan securite + checks statiques")
    p.add_argument("id")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("bundle", help="construit le livrable ZIP")
    p.add_argument("id")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="valide le projet genere (expo export web + tsc)")
    p.add_argument("id")
    p.add_argument("--install", action="store_true", help="lance npm install si node_modules manque")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("preflight", help="verifie ce qui bloque la generation de l'APK")
    p.add_argument("id")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("apk", help="prepare puis lance le build EAS (EXPO_TOKEN requis)")
    p.add_argument("id")
    p.add_argument("--profile", default="preview", choices=["development", "preview", "production"])
    p.add_argument("--execute", action="store_true", help="lance EAS reellement (sinon BUILD_MODE)")
    p.add_argument("--wait", action="store_true", help="attend la fin du build au lieu de rendre la main")
    p.set_defaults(func=cmd_apk)

    p = sub.add_parser("build-status", help="etat du dernier build EAS (et URL de l'APK)")
    p.add_argument("id")
    p.set_defaults(func=cmd_build_status)

    p = sub.add_parser("serve", help="lance le dashboard web")
    p.add_argument("--host", default=config.settings().get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(config.settings().get("PORT", "8080")))
    p.set_defaults(func=cmd_serve)

    sub.add_parser("status", help="etat de l'environnement").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
