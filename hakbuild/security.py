"""Analyse de securite et checks statiques du workspace genere.

Regles alignees sur OWASP Mobile Top 10 / MASVS telles que listees au PRD
section 9.11. Chaque constat est rattache a un fichier et une ligne.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}

SECRET_RULES = [
    ("private_key", "critical", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openrouter_key", "critical", re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("openai_key", "critical", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("aws_key", "critical", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", "critical", re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}")),
    ("slack_token", "critical", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}")),
    ("convex_deploy_key", "critical", re.compile(r"CONVEX_DEPLOY_KEY\s*=\s*\S+")),
    ("telegram_token", "high", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("generic_secret", "high", re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\b\s*[:=]\s*[\"'][^\"']{12,}[\"']"
    )),
]

CODE_RULES = [
    ("eval_usage", "high", re.compile(r"\beval\s*\("), "Execution dynamique de code dans l'application."),
    ("http_cleartext", "medium", re.compile(r"http://(?!localhost|127\.0\.0\.1)"),
     "URL non chiffree en clair."),
    ("client_secret_access", "high", re.compile(r"process\.env\.(?!EXPO_PUBLIC_)[A-Z0-9_]{4,}"),
     "Lecture d'une variable serveur depuis le bundle mobile."),
    ("dangerous_html", "medium", re.compile(r"dangerouslySetInnerHTML"), "Injection HTML cote client."),
    ("broad_storage_permission", "medium",
     re.compile(r"READ_EXTERNAL_STORAGE|WRITE_EXTERNAL_STORAGE|READ_SMS|RECORD_AUDIO"),
     "Permission Android large : justifier aupres du store."),
]

SKIP_DIRS = {"node_modules", ".git", ".expo", "_generated", "__pycache__"}
TEXT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".env", ".yml", ".yaml", ".example"}


def iter_files(root: Path):
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name != ".env":
            continue
        yield path


def scan_workspace(root: Path) -> dict:
    root = Path(root)
    findings: list[dict] = []

    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            client_side = rel.startswith(("app/", "components/", "lib/", "constants/"))
            for rule, severity, pattern in SECRET_RULES:
                if pattern.search(line):
                    findings.append({
                        "rule": rule,
                        "severity": severity,
                        "file": rel,
                        "line": line_no,
                        "message": f"Secret potentiel detecte ({rule}).",
                    })
            for rule, severity, pattern, message in CODE_RULES:
                if rule == "client_secret_access" and not client_side:
                    continue  # cote serveur Convex, process.env est le bon mecanisme
                if pattern.search(line):
                    findings.append({
                        "rule": rule,
                        "severity": severity,
                        "file": rel,
                        "line": line_no,
                        "message": message,
                    })

    findings.extend(_guard_checks(root))

    severities = {f["severity"] for f in findings}
    top = "critical" if "critical" in severities else (
        "high" if "high" in severities else ("medium" if "medium" in severities else "low")
    )
    return {
        "findings": findings,
        "max_severity": top,
        "count": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "high": sum(1 for f in findings if f["severity"] == "high"),
    }


def _guard_checks(root: Path) -> list[dict]:
    findings = []
    convex_dir = root / "convex"
    if convex_dir.exists():
        for path in sorted(convex_dir.glob("*.ts")):
            text = path.read_text(encoding="utf-8", errors="replace")
            exported = len(re.findall(r"export const \w+\s*=\s*(?:query|mutation|action)", text))
            guarded = text.count("ctx.auth.getUserIdentity()")
            if exported and guarded == 0:
                findings.append({
                    "rule": "unguarded_function",
                    "severity": "critical",
                    "file": path.relative_to(root).as_posix(),
                    "line": 1,
                    "message": "Fonction Convex accessible sans verification d'identite.",
                })
            elif exported > guarded:
                findings.append({
                    "rule": "partial_guard",
                    "severity": "high",
                    "file": path.relative_to(root).as_posix(),
                    "line": 1,
                    "message": f"{exported} fonctions exportees mais {guarded} garde(s) d'identite.",
                })
    return findings


def static_checks(root: Path) -> list[dict]:
    """Checks structurels reproductibles, sans dependance npm."""
    root = Path(root)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": "" if ok else detail})

    pkg = root / "package.json"
    data = None
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            add("package.json valide", True)
        except json.JSONDecodeError as exc:
            add("package.json valide", False, str(exc))
    else:
        add("package.json present", False, "fichier absent")

    if data:
        add("dependance expo declaree", "expo" in data.get("dependencies", {}),
            "expo manquant dans dependencies")
        add("script tsc declare", "tsc" in data.get("scripts", {}))

    app_json = root / "app.json"
    if app_json.exists():
        try:
            cfg = json.loads(app_json.read_text(encoding="utf-8"))
            slug = cfg.get("expo", {}).get("slug")
            add("app.json valide avec slug", bool(slug), slug or "slug absent")
        except json.JSONDecodeError as exc:
            add("app.json valide avec slug", False, str(exc))
    else:
        add("app.json present", False, "fichier absent")

    tsconfig = root / "tsconfig.json"
    if tsconfig.exists():
        try:
            json.loads(tsconfig.read_text(encoding="utf-8"))
            add("tsconfig.json valide", True)
        except json.JSONDecodeError as exc:
            add("tsconfig.json valide", False, str(exc))
    else:
        add("tsconfig.json present", False, "fichier absent")

    for rel in ("app/_layout.tsx", "app/index.tsx"):
        path = root / rel
        ok = path.exists() and "export default" in path.read_text(encoding="utf-8", errors="replace")
        add(f"{rel} exporte un composant", ok, "fichier absent ou sans export default")

    schema = root / "convex/schema.ts"
    if schema.exists():
        text = schema.read_text(encoding="utf-8")
        add("convex/schema.ts declare des tables", "defineTable(" in text)
    else:
        add("convex/schema.ts present", False, "fichier absent")

    gitignore = root / ".gitignore"
    ignored = gitignore.exists() and ".env" in gitignore.read_text(encoding="utf-8", errors="replace")
    add(".env ignore par git", ignored, ".gitignore sans .env")

    env_example = root / ".env.example"
    if env_example.exists():
        text = env_example.read_text(encoding="utf-8")
        leaked = bool(re.search(r"(sk-|ghp_|AKIA|xox)", text))
        add(".env.example sans secret", not leaked, "valeur ressemblant a un secret")
    else:
        add(".env.example present", False, "fichier absent")

    return checks


def persist_scan(conn, project_id: str, scan: dict) -> str:
    from .db import new_id, now

    report_id = new_id("sec")
    conn.execute(
        "INSERT INTO security_reports (id, project_id, severity, findings_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (report_id, project_id, scan["max_severity"], json.dumps(scan["findings"], ensure_ascii=False), now()),
    )
    conn.commit()
    return report_id


def persist_checks(conn, project_id: str, checks: list[dict]) -> tuple[str, int, int]:
    from .db import new_id, now

    passed = sum(1 for c in checks if c["ok"])
    failed = len(checks) - passed
    result_id = new_id("tst")
    conn.execute(
        "INSERT INTO test_results (id, project_id, suite, passed, failed, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (result_id, project_id, "static", passed, failed, json.dumps(checks, ensure_ascii=False), now()),
    )
    conn.commit()
    return result_id, passed, failed
