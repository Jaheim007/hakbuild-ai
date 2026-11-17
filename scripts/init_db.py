"""Initialise la base HakBuild (migrations seules, aucun projet cree)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hakbuild import config, db  # noqa: E402


def main() -> int:
    conn = db.connect()
    applied = db.migrate(conn)
    rows = conn.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall()
    print(f"Base          : {config.db_path()}")
    print(f"Migrations    : {applied} appliquee(s) maintenant, {len(db.MIGRATIONS)} au total")
    for row in rows:
        print(f"  - {row['version']} ({row['applied_at']})")
    projets = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    print(f"Projets       : {projets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
