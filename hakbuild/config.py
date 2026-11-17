"""Configuration HakBuild AI.

Aucune dependance externe : lecture de .env a la demande (jamais mise en cache
au moment de l'import), pour qu'un script qui cree .env juste avant de lancer
une commande voie bien les nouvelles valeurs.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

SECRET_KEYS = (
    "OPENROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
    "CONVEX_DEPLOY_KEY",
    "EAS_TOKEN",
    "CLERK_SECRET_KEY",
)

DEFAULTS = {
    "DATABASE_URL": "sqlite:///data/hakbuild.db",
    "HOST": "127.0.0.1",
    "PORT": "8080",
    "BUILD_MODE": "dry-run",  # dry-run | execute
    "DATA_DIR": "data",
}


def read_env_file(path: Path | None = None) -> dict:
    """Parse un fichier .env simple (KEY=VALUE, commentaires #)."""
    path = Path(path) if path else ENV_FILE
    if not path.exists():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def settings() -> dict:
    """Vue unifiee : defauts < .env < variables d'environnement."""
    data = dict(DEFAULTS)
    data.update(read_env_file())
    for key in list(data) + list(SECRET_KEYS):
        if key in os.environ and os.environ[key] != "":
            data[key] = os.environ[key]
    return data


def data_dir() -> Path:
    path = Path(settings().get("DATA_DIR", "data"))
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    """DATABASE_URL = sqlite:///chemin -> Path. Un ':memory:' reste en memoire."""
    url = settings().get("DATABASE_URL", DEFAULTS["DATABASE_URL"])
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        raw = url[len("sqlite://"):]
    else:
        raw = url
    if raw in (":memory:", "memory:", ""):
        return Path(":memory:")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def workspace_dir(project_id: str) -> Path:
    path = data_dir() / "projects" / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def deliverables_dir() -> Path:
    path = data_dir() / "deliverables"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configured_secrets() -> dict:
    """Indique quels secrets sont presents, sans jamais retourner leur valeur."""
    data = settings()
    return {key: bool(data.get(key)) for key in SECRET_KEYS}


def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return "(absent)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"
