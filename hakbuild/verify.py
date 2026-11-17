"""Validation réelle du projet généré : bundling Expo Web + TypeScript.

C'est la seule vérification qui prouve que l'application générée compile.
`expo export` ne demande aucun compte ; `tsc` signale les erreurs Convex
uniquement quand `convex/_generated` n'existe pas (deployment absent).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, db, runner


def _save(conn, project_id: str, suite: str, passed: int, failed: int, detail: dict) -> None:
    conn.execute(
        "INSERT INTO test_results (id, project_id, suite, passed, failed, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (db.new_id("tst"), project_id, suite, passed, failed, json.dumps(detail, ensure_ascii=False), db.now()),
    )
    conn.commit()


def verify_web(conn, project_id: str, install: bool = False, timeout: int = 900) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"ok": False, "error": "projet inconnu"}
    workspace = Path(project["workspace"])
    if not (workspace / "package.json").exists():
        return {"ok": False, "error": "aucun package.json : lancer le pipeline jusqu'a DEVELOPING"}

    has_modules = (workspace / "node_modules").exists()
    if not has_modules:
        if not install:
            return {
                "ok": False,
                "status": "missing_deps",
                "detail": "node_modules absent : relancer avec --install",
            }
        install_run = runner.run(conn, project_id, runner.STEPS["npm_install"], workspace, timeout=timeout)
        if install_run["exit_code"] != 0:
            return {"ok": False, "status": "npm_failed", "exit_code": install_run["exit_code"],
                    "output": install_run["output"][-1200:]}

    export_run = runner.run(conn, project_id, "npx expo export --platform web", workspace, timeout=timeout)
    output = export_run["output"]
    dist = workspace / "dist"

    # lecture deterministe du resultat sur disque plutot que sur la sortie CLI
    routes: list[str] = []
    bundle = None
    exported = False
    if dist.exists():
        pages = sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*.html"))
        exported = "index.html" in pages
        routes = [p for p in pages if not p.startswith("_")]
        js_files = sorted(dist.glob("_expo/static/js/web/*.js"), key=lambda p: p.stat().st_size, reverse=True)
        if js_files:
            size_mb = js_files[0].stat().st_size / (1024 * 1024)
            bundle = f"{js_files[0].name} ({size_mb:.1f} MB)"

    _save(conn, project_id, "expo-web", 1 if exported else 0, 0 if exported else 1, {
        "exit_code": export_run["exit_code"],
        "routes": routes,
        "bundle": bundle,
        "dist": str(dist) if exported else None,
    })

    tsc_run = runner.run(conn, project_id, runner.STEPS["tsc"], workspace, timeout=timeout)
    errors = [line for line in tsc_run["output"].splitlines() if ": error TS" in line]
    generated_missing = any("_generated" in line for line in errors)
    app_errors = [line for line in errors if not line.startswith("convex/")]

    detail = {
        "total_errors": len(errors),
        "app_errors": app_errors[:20],
        "convex_codegen_missing": generated_missing,
        "note": (
            "Les erreurs convex/*.ts viennent de l'absence de convex/_generated "
            "(deployment Convex non cree) : `npx convex dev` les resout."
        ) if generated_missing else "",
    }
    _save(conn, project_id, "tsc", 1 if not app_errors else 0, len(app_errors), detail)

    if exported:
        db.log(
            conn,
            f"Build web verifie: {len(routes)} routes, bundle {bundle or '?'}, "
            f"{len(app_errors)} erreur(s) TS applicative(s)",
            project_id,
        )
    else:
        db.log(conn, f"Build web en echec (exit {export_run['exit_code']})", project_id, "error")

    return {
        "ok": exported and not app_errors,
        "exported": exported,
        "exit_code": export_run["exit_code"],
        "routes": routes,
        "bundle": bundle,
        "dist": str(dist) if exported else None,
        "tsc": detail,
        "output_tail": output[-1500:] if not exported else "",
        "install_path_note": "npm install execute" if not has_modules and install else "",
        "build_mode": config.settings().get("BUILD_MODE"),
    }
