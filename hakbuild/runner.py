"""Execution de commandes avec liste blanche, timeout et journalisation reelle.

Aucun resultat n'est invente : si la commande n'existe pas ou si les
dependances manquent, l'echec est enregistre tel quel et remonte au projet.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .db import log, new_id, now

ALLOWED_PREFIXES = (
    "npx ",
    "npm ",
    "node ",
    "git ",
    "gh ",
    "python ",
    "python3 ",
    "expo ",
    "eas ",
    "convex ",
    "adb ",
    "maestro ",
)

DEFAULT_TIMEOUT = 600


class CommandRefused(Exception):
    pass


def is_allowed(command: str) -> bool:
    cmd = command.strip()
    return any(cmd.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def run(conn, project_id: str, command: str, cwd: Path, timeout: int = DEFAULT_TIMEOUT,
        task_id: str | None = None, env: dict | None = None) -> dict:
    """Execute une commande autorisee et enregistre la preuve dans agent_runs."""
    if not is_allowed(command):
        raise CommandRefused(f"commande hors liste blanche: {command}")

    executable = command.split()[0]
    child_env = dict(os.environ)
    if env:
        child_env.update({k: v for k, v in env.items() if v})
    if shutil.which(executable, path=child_env.get("PATH")) is None:
        result = {
            "id": new_id("run"),
            "command": command,
            "exit_code": 127,
            "output": f"binaire introuvable sur cette machine: {executable}",
            "duration_ms": 0,
            "status": "missing",
        }
        _store(conn, project_id, task_id, result)
        log(conn, f"[RUN] {command} -> binaire absent ({executable})", project_id, "warn", "runner")
        return result

    started = time.time()
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
        status = "ok" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired:
        output = f"timeout apres {timeout}s"
        exit_code = 124
        status = "timeout"
    except OSError as exc:  # pragma: no cover - environnement
        output = str(exc)
        exit_code = 126
        status = "error"

    duration_ms = int((time.time() - started) * 1000)
    result = {
        "id": new_id("run"),
        "command": command,
        "exit_code": exit_code,
        "output": output[-8000:],
        "duration_ms": duration_ms,
        "status": status,
    }
    _store(conn, project_id, task_id, result)
    log(conn, f"[RUN] {command} -> {exit_code} ({duration_ms} ms)", project_id,
        "info" if status == "ok" else "error", "runner")
    return result


def _store(conn, project_id: str, task_id: str | None, result: dict) -> None:
    conn.execute(
        "INSERT INTO agent_runs (id, project_id, task_id, command, exit_code, output, duration_ms, started_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            result["id"],
            project_id,
            task_id,
            result["command"],
            result["exit_code"],
            result["output"],
            result["duration_ms"],
            now(),
            now(),
        ),
    )
    conn.commit()


def toolchain_report() -> dict:
    tools = ["node", "npm", "npx", "git", "python", "adb", "maestro"]
    return {tool: shutil.which(tool) is not None for tool in tools}


STEPS = {
    "npm_install": "npm install --no-audit --no-fund",
    "tsc": "npx tsc --noEmit",
    "git_init": "git init -b main",
    "convex_dev": "npx convex dev --once",
    "eas_build": "npx eas-cli build --platform android --profile preview --non-interactive",
}
