"""Déploiement du backend Convex d'une application générée.

La clé de déploiement (`prod:<deployment>|<secret>`) donne le nom du deployment,
donc l'URL publique `https://<deployment>.convex.cloud`. Elle est fournie via
l'environnement du processus enfant, jamais en argument ni dans un fichier suivi.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config, db, runner

KEY_RE = re.compile(r"^(?:prod|dev):([^|]+)\|")
GENERATED_API = """/**
 * API Convex typée, générée par `npx convex deploy` / `convex dev`.
 * Repli automatique sur anyApi si le code généré n'existe pas encore.
 */
import { api as generated } from "../convex/_generated/api";

export const api = generated;
"""
FALLBACK_API = """import { anyApi } from "convex/server";

/** Reference non typée, utilisée tant qu'aucun deployment Convex n'existe. */
export const api = anyApi;
"""


def deploy_key() -> str | None:
    return config.settings().get("CONVEX_DEPLOY_KEY") or None


def deployment_from_key(key: str) -> str | None:
    match = KEY_RE.match(key or "")
    return match.group(1) if match else None


def deployment_url(deployment: str) -> str:
    return f"https://{deployment}.convex.cloud"


def write_app_env(workspace: Path, url: str) -> Path:
    path = Path(workspace) / ".env"
    path.write_text(
        "# Généré par HakBuild : URL publique du deployment Convex (pas un secret)\n"
        f"EXPO_PUBLIC_CONVEX_URL={url}\n",
        encoding="utf-8",
    )
    return path


def set_eas_env(workspace: Path, url: str) -> bool:
    """Injecte l'URL dans les profils eas.json (`.env` est exclu de l'archive EAS)."""
    path = Path(workspace) / "eas.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    for profile in ("preview", "production", "development"):
        block = data.get("build", {}).get(profile)
        if isinstance(block, dict):
            block.setdefault("env", {})["EXPO_PUBLIC_CONVEX_URL"] = url
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def deploy(conn, project_id: str, switch_api: bool = True) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"ok": False, "error": "projet inconnu"}
    key = deploy_key()
    if not key:
        return {
            "ok": False,
            "status": "no_key",
            "error": "CONVEX_DEPLOY_KEY absent de .env",
            "hint": "dashboard.convex.dev -> projet -> Settings -> Deploy keys -> production key",
        }
    deployment = deployment_from_key(key)
    if not deployment:
        return {"ok": False, "status": "bad_key", "error": "format de clé inattendu (prod:<deployment>|...)"}

    workspace = Path(project["workspace"])
    if not (workspace / "convex" / "schema.ts").exists():
        return {"ok": False, "status": "not_ready", "error": "convex/schema.ts absent : lancer le pipeline"}

    url = deployment_url(deployment)
    write_app_env(workspace, url)
    set_eas_env(workspace, url)

    result = runner.run(
        conn, project_id,
        "npx convex deploy --yes",
        workspace,
        timeout=900,
        env={"CONVEX_DEPLOY_KEY": key, "CI": "1"},
    )
    generated = (workspace / "convex" / "_generated" / "api.d.ts").exists()

    if switch_api:
        api_file = workspace / "lib" / "api.ts"
        api_file.parent.mkdir(parents=True, exist_ok=True)
        api_file.write_text(GENERATED_API if generated else FALLBACK_API, encoding="utf-8")

    tsc = runner.run(conn, project_id, runner.STEPS["tsc"], workspace, timeout=600)
    errors = [line for line in tsc["output"].splitlines() if ": error TS" in line]

    ok = result["exit_code"] == 0 and generated
    conn.execute(
        "UPDATE projects SET convex_project = ?, updated_at = ? WHERE id = ?",
        (url if ok else None, db.now(), project_id),
    )
    conn.execute(
        "INSERT INTO integrations (id, project_id, name, status, config_json, created_at)"
        " VALUES (?, ?, 'convex', ?, ?, ?)",
        (
            db.new_id("int"),
            project_id,
            "deployed" if ok else "failed",
            json.dumps({"deployment": deployment, "url": url, "generated": generated}),
            db.now(),
        ),
    )
    conn.commit()
    db.log(
        conn,
        f"Convex deploy {deployment}: {'ok' if ok else 'echec'} (codegen={generated}, erreurs TS={len(errors)})",
        project_id,
        "info" if ok else "error",
    )
    return {
        "ok": ok,
        "status": "deployed" if ok else "failed",
        "deployment": deployment,
        "url": url,
        "generated": generated,
        "exit_code": result["exit_code"],
        "ts_errors": len(errors),
        "api_switched": switch_api,
        "output_tail": result["output"][-1200:],
    }


def status(conn, project_id: str) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"ok": False, "error": "projet inconnu"}
    workspace = Path(project["workspace"])
    app_env = workspace / ".env"
    url = None
    if app_env.exists():
        for line in app_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("EXPO_PUBLIC_CONVEX_URL="):
                url = line.split("=", 1)[1].strip()
    return {
        "key_present": bool(deploy_key()),
        "deployment": deployment_from_key(deploy_key() or "") if deploy_key() else None,
        "app_url": url,
        "generated": (workspace / "convex" / "_generated" / "api.d.ts").exists(),
        "project": project["convex_project"],
    }
