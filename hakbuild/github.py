"""Publication d'un projet généré sur GitHub (PRD section 9.8).

S'appuie sur `gh` (déjà authentifié sur cette machine, dépôt privé par défaut).
Aucun jeton n'est passé en argument : gh lit son credential store.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import db, runner

CANDIDATES = [
    Path.home() / "gh-portable" / "bin",
    Path("C:/Program Files/GitHub CLI"),
    Path("C:/Program Files (x86)/GitHub CLI"),
]


def gh_dir() -> Path | None:
    found = shutil.which("gh")
    if found:
        return Path(found).parent
    for candidate in CANDIDATES:
        for name in ("gh.exe", "gh"):
            if (candidate / name).exists():
                return candidate
    return None


def gh_env() -> dict:
    directory = gh_dir()
    env = {}
    if directory:
        env["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    return env


def available(conn=None) -> dict:
    if gh_dir() is None:
        return {"installed": False, "authenticated": False, "reason": "gh introuvable"}
    probe = shutil.which("gh", path=gh_env()["PATH"]) or "gh"
    import subprocess

    try:
        out = subprocess.run([probe, "api", "user", "--jq", ".login"],
                             capture_output=True, text=True, timeout=60, env={**os.environ, **gh_env()})
    except (OSError, subprocess.SubprocessError) as exc:
        return {"installed": True, "authenticated": False, "reason": str(exc)}
    if out.returncode != 0:
        return {"installed": True, "authenticated": False, "reason": (out.stderr or "").strip()[:200]}
    return {"installed": True, "authenticated": True, "account": out.stdout.strip()}


def repo_name(project: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (project.get("name") or "app").lower()).strip("-")
    return slug or "app"


def push_project(conn, project_id: str, name: str | None = None, private: bool = True) -> dict:
    project = db.get_project(conn, project_id)
    if project is None:
        return {"ok": False, "error": "projet inconnu"}
    state = available(conn)
    if not state.get("authenticated"):
        return {"ok": False, "error": "gh non authentifie", "detail": state.get("reason"), "status": "no_auth"}

    workspace = Path(project["workspace"])
    if not (workspace / "package.json").exists():
        return {"ok": False, "error": "projet vide : lancer le pipeline jusqu'a DEVELOPING", "status": "not_ready"}

    repo = name or repo_name(dict(project))
    account = state["account"]
    url = f"https://github.com/{account}/{repo}.git"

    remote = runner.run(conn, project_id, "git remote get-url origin", workspace, timeout=30, env=gh_env())
    if remote["exit_code"] == 0:
        push = runner.run(conn, project_id, "git push -u origin main", workspace, timeout=300, env=gh_env())
        created = False
    else:
        if not (workspace / ".git").exists():
            runner.run(conn, project_id, "git init -b main", workspace, timeout=60)
            runner.run(conn, project_id, "git add -A", workspace, timeout=120)
            runner.run(
                conn, project_id,
                'git -c user.email=agent@hakbuild.local -c user.name=HakBuild commit -m "initial: application generee" --allow-empty',
                workspace, timeout=120,
            )
        flag = "--private" if private else "--public"
        create = runner.run(
            conn, project_id,
            f"gh repo create {repo} {flag} --source=. --remote=origin --push",
            workspace, timeout=600, env=gh_env(),
        )
        created = create["exit_code"] == 0
        if not created:
            return {"ok": False, "status": "create_failed", "output": create["output"][-600:]}
        push = {"exit_code": 0, "output": create["output"][-300:]}

    if push.get("exit_code") != 0:
        return {"ok": False, "status": "push_failed", "output": push.get("output", "")[-600:]}

    conn.execute(
        "UPDATE projects SET repo_url = ?, updated_at = ? WHERE id = ?",
        (url, db.now(), project_id),
    )
    conn.execute(
        "INSERT INTO repositories (id, project_id, provider, url, visibility, created_at) VALUES (?, ?, 'github', ?, ?, ?)",
        (db.new_id("repo"), project_id, url, "private" if private else "public", db.now()),
    )
    conn.commit()
    db.audit(conn, "agent", "github.push", project_id, {"url": url, "created": created})
    db.log(conn, f"Code pousse sur GitHub: {url}", project_id)
    return {"ok": True, "status": "pushed", "url": url.replace(".git", ""), "created": created, "account": account}
