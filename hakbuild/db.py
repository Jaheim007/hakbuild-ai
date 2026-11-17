"""Acces SQLite, migrations et helpers communs (stdlib uniquement)."""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from . import config

MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_init",
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            plan TEXT NOT NULL DEFAULT 'free',
            credits INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            idea TEXT NOT NULL,
            platforms TEXT NOT NULL DEFAULT 'android',
            status TEXT NOT NULL DEFAULT 'PENDING',
            workspace TEXT,
            repo_url TEXT,
            convex_project TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, created_at);

        CREATE TABLE IF NOT EXISTS prd_documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            validated_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE INDEX IF NOT EXISTS idx_prd_project ON prd_documents(project_id, version);

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            job_id TEXT,
            title TEXT NOT NULL,
            phase TEXT NOT NULL,
            detail TEXT,
            done_criteria TEXT,
            status TEXT NOT NULL DEFAULT 'TODO',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status);

        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT,
            command TEXT NOT NULL,
            exit_code INTEGER,
            output TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            level TEXT NOT NULL DEFAULT 'info',
            source TEXT NOT NULL DEFAULT 'agent',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_project ON logs(project_id, id);

        CREATE TABLE IF NOT EXISTS builds (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            profile TEXT NOT NULL,
            status TEXT NOT NULL,
            artifact_path TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            gate TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_project ON approvals(project_id, status);

        CREATE TABLE IF NOT EXISTS test_results (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            suite TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            detail TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS security_reports (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_transactions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            meta TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id, created_at);

        CREATE TABLE IF NOT EXISTS integrations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'declared',
            config_json TEXT,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        "002_project_metrics",
        """
        ALTER TABLE projects ADD COLUMN credits_spent INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        "003_task_code",
        """
        ALTER TABLE tasks ADD COLUMN code TEXT;
        """,
    ),
    (
        "004_task_evidence",
        """
        ALTER TABLE tasks ADD COLUMN evidence TEXT;
        """,
    ),
]


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def connect(path=None) -> sqlite3.Connection:
    target = Path(path) if path else config.db_path()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Applique les migrations manquantes. Retourne le nombre applique."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    count = 0
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, now())
        )
        count += 1
    conn.commit()
    return count


def log(conn, message: str, project_id: str | None = None, level: str = "info", source: str = "agent") -> None:
    conn.execute(
        "INSERT INTO logs (project_id, level, source, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, level, source, message, now()),
    )
    conn.commit()


def audit(conn, actor: str, action: str, target: str | None = None, meta: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor, action, target, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target, json.dumps(meta or {}, ensure_ascii=False), now()),
    )
    conn.commit()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_artifact(conn, project_id: str, kind: str, path: Path) -> str:
    path = Path(path)
    artifact_id = new_id("art")
    conn.execute(
        "INSERT INTO artifacts (id, project_id, kind, path, size, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            artifact_id,
            project_id,
            kind,
            str(path),
            path.stat().st_size if path.exists() else 0,
            sha256_file(path) if path.exists() and path.is_file() else None,
            now(),
        ),
    )
    conn.commit()
    return artifact_id


def get_project(conn, project_id: str):
    return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def next_prd_version(conn, project_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM prd_documents WHERE project_id = ?", (project_id,)
    ).fetchone()
    return int(row["v"]) + 1
