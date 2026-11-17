"""Préparation et exécution du build EAS (APK Android).

Sans jeton Expo, la fonction ne simule rien : elle renvoie la liste exacte des
blocages. Avec EXPO_TOKEN (ou EAS_TOKEN), elle prépare le projet et lance un
build réel, dont l'URL est enregistrée comme preuve.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config, db, runner

BUILD_URL = re.compile(r"https://expo\.dev/[^\s]*builds/[0-9a-f-]{8,}", re.I)


def _load_app_json(workspace: Path) -> dict:
    path = workspace / "app.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def token() -> str | None:
    data = config.settings()
    return data.get("EXPO_TOKEN") or data.get("EAS_TOKEN") or None


def preflight(conn, project_id: str) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"ready": False, "blockers": ["projet inconnu"], "checks": []}
    workspace = Path(project["workspace"])
    app_cfg = _load_app_json(workspace)
    expo = app_cfg.get("expo", {})
    eas_project = (expo.get("extra") or {}).get("eas", {}).get("projectId")

    checks: list[dict] = []

    def add(key: str, label: str, ok: bool, hint: str, blocking: bool = True) -> None:
        checks.append({"key": key, "label": label, "ok": ok, "hint": "" if ok else hint, "blocking": blocking})

    add("package_json", "package.json", (workspace / "package.json").exists(), "lancer le pipeline jusqu'a DEVELOPING")
    add("eas_json", "eas.json", (workspace / "eas.json").exists(), "profil de build absent")
    add("app_json", "app.json avec slug", bool(expo.get("slug")), "slug Expo manquant")
    add("android_package", "package Android", bool((expo.get("android") or {}).get("package")),
        "com.hakbuild.<slug> attendu")
    add("deps", "dependances npm installees", (workspace / "node_modules").exists(),
        "`npm install` (ou verify --install)")
    git_ok = (workspace / ".git").exists()
    commits = 0
    if git_ok:
        proc = runner.run(conn, project_id, "git rev-list --count HEAD", workspace, timeout=30)
        commits = int(proc["output"].strip() or 0) if proc["exit_code"] == 0 else 0
    add("git", "depot git avec un commit", git_ok and commits > 0, "`git init` + commit (EAS archive un commit)")
    add("expo_token", "jeton Expo (EXPO_TOKEN)", bool(token()),
        "creer un token sur expo.dev/settings/access-tokens et le mettre dans .env (jamais dans le chat)")
    add("eas_project", "projet EAS lie", bool(eas_project),
        "`npx eas-cli init` (fait automatiquement si le jeton est present)")

    blockers = [c["hint"] for c in checks if not c["ok"] and c["blocking"]]
    return {
        "ready": not blockers,
        "blockers": blockers,
        "checks": checks,
        "workspace": str(workspace),
        "platform": "android",
        "profile": "preview",
        "build_mode": config.settings().get("BUILD_MODE"),
        "eas_project_id": eas_project,
        "git_commits": commits,
    }


def _git_commit(conn, project_id: str, workspace: Path) -> str | None:
    if not (workspace / ".git").exists():
        init = runner.run(conn, project_id, "git init -b main", workspace, timeout=60)
        if init["exit_code"] != 0:
            return f"git init: {init['output'][-200:]}"
    runner.run(conn, project_id, "git add -A", workspace, timeout=120)
    commit = runner.run(
        conn,
        project_id,
        'git -c user.email=build@hakbuild.local -c user.name=HakBuild '
        'commit -m "build: snapshot pour EAS" --allow-empty',
        workspace,
        timeout=120,
    )
    return None if commit["exit_code"] == 0 else f"git commit: {commit['output'][-200:]}"


def _eas_init(conn, project_id: str, workspace: Path) -> tuple[bool, str]:
    result = runner.run(
        conn,
        project_id,
        "npx eas-cli init --non-interactive --force",
        workspace,
        timeout=300,
        env={"EXPO_TOKEN": token() or ""},
    )
    return result["exit_code"] == 0, result["output"][-600:]


def build_apk(conn, project_id: str, profile: str = "preview", execute: bool | None = None) -> dict:
    state = preflight(conn, project_id)
    workspace = Path(state["workspace"])
    if not token():
        return {"ok": False, "status": "blocked", "blockers": state["blockers"], "preflight": state}

    if execute is None:
        execute = config.settings().get("BUILD_MODE") == "execute"

    # preparation : commits + liaison du projet EAS
    if not state["checks"][5]["ok"]:
        error = _git_commit(conn, project_id, workspace)
        if error:
            return {"ok": False, "status": "git_failed", "detail": error, "blockers": state["blockers"]}
    if not state["eas_project_id"]:
        ok, output = _eas_init(conn, project_id, workspace)
        if not ok:
            return {"ok": False, "status": "eas_init_failed", "output": output, "blockers": state["blockers"]}

    command = f"npx eas-cli build --platform android --profile {profile} --non-interactive"
    if not execute:
        db.log(conn, f"APK pret (BUILD_MODE dry-run) : {command}", project_id)
        return {
            "ok": True,
            "status": "ready",
            "command": command,
            "detail": "BUILD_MODE=execute pour lancer reellement",
            "preflight": preflight(conn, project_id),
        }

    run = runner.run(conn, project_id, command, workspace, timeout=3600, env={"EXPO_TOKEN": token() or ""})
    match = BUILD_URL.search(run["output"])
    build_id = db.new_id("bld")
    status = "queued" if run["exit_code"] == 0 else "failed"
    conn.execute(
        "INSERT INTO builds (id, project_id, platform, profile, status, artifact_path, note, created_at, updated_at)"
        " VALUES (?, ?, 'android', ?, ?, ?, ?, ?, ?)",
        (build_id, project_id, profile, status, match.group(0) if match else None,
         f"EAS exit {run['exit_code']} : {run['output'][-400:]}", db.now(), db.now()),
    )
    conn.commit()
    db.log(conn, f"EAS build {status} (exit {run['exit_code']})", project_id, "info" if run["exit_code"] == 0 else "error")
    return {
        "ok": run["exit_code"] == 0,
        "status": status,
        "build_url": match.group(0) if match else None,
        "exit_code": run["exit_code"],
        "output_tail": run["output"][-1500:],
    }


def eas_json() -> str:
    return json.dumps(
        {
            "cli": {"version": ">= 5.0.0", "appVersionSource": "remote"},
            "build": {
                "development": {"developmentClient": True, "distribution": "internal"},
                "preview": {"distribution": "internal", "android": {"buildType": "apk"}},
                "production": {"autoIncrement": True, "android": {"buildType": "app-bundle"}},
            },
            "submit": {"production": {}},
        },
        indent=2,
    ) + "\n"


def env_with_token() -> dict:
    """Environnement d'execution : le jeton n'apparait jamais dans la commande."""
    base = {"EXPO_TOKEN": token() or ""} if token() else {}
    base.update({"CI": os.environ.get("CI", "1")})
    return base
