"""Collecte des preuves et constitution du livrable final (PRD section 9.13).

Le bundle est un ZIP verifie : on le relit avec zipfile avant de le declarer
livrable. Un livrable incomplet est signale comme tel, jamais masque.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from . import config, db

CHECKLIST = [
    ("prd", "PRD valide", "prd.md"),
    ("source", "Code source de l'application", "inventory.json"),
    ("github", "Repository GitHub", None),
    ("tests", "Rapport de tests", "tests.json"),
    ("security", "Rapport de securite", "security.json"),
    ("install", "Instructions d'installation", "INSTALL.md"),
    ("docs", "Documentation technique", "TECHNICAL.md"),
    ("backend", "Informations backend Convex", "backend.json"),
    ("apk", "APK / build Android", None),
    ("screens", "Captures d'ecran", None),
]


def _sha(path: Path) -> str:
    return db.sha256_file(path)


def inventory(workspace: Path) -> list[dict]:
    entries = []
    workspace = Path(workspace)
    if not workspace.exists():
        return entries
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or "node_modules" in path.parts or ".git" in path.parts:
            continue
        entries.append(
            {
                "path": path.relative_to(workspace).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    return entries


def collect(conn, project_id: str) -> dict:
    project = dict(db.get_project(conn, project_id))
    prd_row = conn.execute(
        "SELECT * FROM prd_documents WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)
    ).fetchone()
    tasks = [dict(r) for r in conn.execute(
        "SELECT code, title, phase, status, attempts, evidence, last_error FROM tasks WHERE project_id = ? ORDER BY code",
        (project_id,),
    )]
    tests = [dict(r) for r in conn.execute(
        "SELECT suite, passed, failed, detail, created_at FROM test_results WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
        (project_id,),
    )]
    security_rows = [dict(r) for r in conn.execute(
        "SELECT severity, findings_json, created_at FROM security_reports WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
        (project_id,),
    )]
    builds = [dict(r) for r in conn.execute(
        "SELECT id, platform, profile, status, artifact_path, note, created_at FROM builds WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )]
    artifacts = [dict(r) for r in conn.execute(
        "SELECT kind, path, size, sha256, created_at FROM artifacts WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )]
    integrations = [dict(r) for r in conn.execute(
        "SELECT name, status FROM integrations WHERE project_id = ? ORDER BY name", (project_id,)
    )]
    return {
        "project": project,
        "prd": dict(prd_row) if prd_row else None,
        "tasks": tasks,
        "tests": tests,
        "security": security_rows,
        "builds": builds,
        "artifacts": artifacts,
        "integrations": integrations,
        "inventory": inventory(Path(project["workspace"])) if project.get("workspace") else [],
    }


def checklist(data: dict) -> list[dict]:
    project = data["project"]
    apk = next((b for b in data["builds"] if b.get("artifact_path")), None)
    state = {
        "prd": bool(data["prd"]),
        "source": len(data["inventory"]) > 0,
        "github": bool(project.get("repo_url")),
        "tests": bool(data["tests"]),
        "security": bool(data["security"]),
        "install": True,
        "docs": True,
        "backend": bool(data["inventory"]) or bool(project.get("convex_project")),
        "apk": bool(apk),
        "screens": False,
    }
    return [
        {"key": key, "label": label, "present": state[key], "file": filename}
        for key, label, filename in CHECKLIST
    ]


def _report_md(data: dict, checks: list[dict]) -> str:
    project = data["project"]
    lines = [
        f"# Rapport de livraison - {project['name']}",
        "",
        f"- Projet : `{project['id']}`",
        f"- Statut : **{project['status']}**",
        f"- Cree le : {project['created_at']}",
        f"- Plateformes : {project['platforms']}",
        "",
        "## Livrables",
        "",
        "| Element | Etat | Fichier |",
        "|---|---|---|",
    ]
    for check in checks:
        icon = "present" if check["present"] else "**absent**"
        lines.append(f"| {check['label']} | {icon} | {check['file'] or '-'} |")

    lines += ["", "## Taches", "", "| Code | Tache | Phase | Statut | Preuve |", "|---|---|---|---|---|"]
    for task in data["tasks"]:
        evidence = (task.get("evidence") or task.get("last_error") or "").replace("|", "/")[:120]
        lines.append(
            f"| {task['code']} | {task['title']} | {task['phase']} | {task['status']} | {evidence} |"
        )

    lines += ["", "## Tests", ""]
    if data["tests"]:
        for test in data["tests"]:
            lines.append(f"- {test['created_at']} : {test['passed']} ok / {test['failed']} echec(s)")
    else:
        lines.append("- Aucun test enregistre.")

    lines += ["", "## Securite", ""]
    if data["security"]:
        for report in data["security"]:
            findings = json.loads(report["findings_json"])
            lines.append(f"- {report['created_at']} : severite max `{report['severity']}`, {len(findings)} constat(s)")
            for finding in findings[:10]:
                lines.append(
                    f"  - [{finding['severity']}] {finding['rule']} - {finding['file']}:{finding['line']} ({finding['message']})"
                )
    else:
        lines.append("- Aucun rapport de securite.")

    lines += ["", "## Builds", ""]
    if data["builds"]:
        for build in data["builds"]:
            lines.append(f"- {build['platform']}/{build['profile']} : `{build['status']}` - {build.get('note') or ''}")
    else:
        lines.append("- Aucun build enregistre.")

    lines += ["", "## Integrations", ""]
    for integration in data["integrations"]:
        lines.append(f"- {integration['name']} : {integration['status']}")

    lines += [
        "",
        "## Limites connues",
        "",
        "- La compilation reelle exige un compte Expo/EAS et le mode `BUILD_MODE=execute`.",
        "- Les captures d'ecran necessitent un emulateur ou un appareil connecte.",
        "- La publication sur les stores reste une action humaine.",
        "",
    ]
    return "\n".join(lines)


INSTALL_MD = """# Installation

## Prerequis

- Node.js 20+
- Un compte Expo (gratuit) et un compte Convex

## Etapes

```bash
npm install
cp .env.example .env
npx convex dev
npx expo start
```

## APK

```bash
npx eas-cli login
npx eas-cli build --platform android --profile preview
```

Le lien de telechargement est affiche a la fin du build.
"""

TECHNICAL_MD = """# Documentation technique

## Architecture

- Mobile : React Native + Expo Router + TypeScript.
- Backend : projet Convex dedie (separation niveau 2 du PRD).
- Auth : Convex Auth, verification d'identite dans chaque fonction.

## Conventions

- `ownerId` sur chaque table metier ; index `by_owner`.
- Aucune cle privee dans le bundle mobile : seules les variables `EXPO_PUBLIC_*`.
- Les mutations valident les entrees cote serveur.

## Commandes utiles

| But | Commande |
|---|---|
| Verifier les types | `npx tsc --noEmit` |
| Deployer le backend | `npx convex dev --once` |
| Compiler Android | `npx eas-cli build -p android --profile preview` |
"""


def build_bundle(conn, project_id: str) -> Path:
    data = collect(conn, project_id)
    checks = checklist(data)
    project = data["project"]
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in project["name"].lower())[:40] or "app"

    out_root = config.deliverables_dir() / project_id
    stage = out_root / "bundle"
    if stage.exists():
        shutil.rmtree(stage)
    for sub in ("source", "reports"):
        (stage / sub).mkdir(parents=True, exist_ok=True)

    (stage / "reports" / "README_REPORT.md").write_text(_report_md(data, checks), encoding="utf-8")
    (stage / "reports" / "INSTALL.md").write_text(INSTALL_MD, encoding="utf-8")
    (stage / "reports" / "TECHNICAL.md").write_text(TECHNICAL_MD, encoding="utf-8")
    (stage / "reports" / "tests.json").write_text(json.dumps(data["tests"], indent=2, ensure_ascii=False), encoding="utf-8")
    (stage / "reports" / "security.json").write_text(
        json.dumps(data["security"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (stage / "reports" / "inventory.json").write_text(
        json.dumps(data["inventory"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (stage / "reports" / "backend.json").write_text(
        json.dumps(
            {
                "convex_project": project.get("convex_project"),
                "tables": [e["path"] for e in data["inventory"] if e["path"].startswith("convex/")],
                "integrations": data["integrations"],
                "note": "Secrets a definir via `npx convex env set`, jamais dans le depot.",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (stage / "reports" / "tasks.json").write_text(json.dumps(data["tasks"], indent=2, ensure_ascii=False), encoding="utf-8")

    if data["prd"]:
        (stage / "prd.md").write_text(data["prd"]["content"], encoding="utf-8")

    workspace = Path(project["workspace"]) if project.get("workspace") else None
    copied = 0
    if workspace and workspace.exists():
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            if any(part in {"node_modules", ".git", ".expo"} for part in path.parts):
                continue
            target = stage / "source" / path.relative_to(workspace)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1

    zip_path = out_root / f"{slug}-bundle.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())

    # verification neutre du ZIP produit
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        names = archive.namelist()
    if bad is not None or not names:
        raise RuntimeError(f"bundle ZIP invalide (entree corrompue: {bad})")

    db.record_artifact(conn, project_id, "bundle", zip_path)
    db.log(conn, f"Bundle livre: {zip_path.name} ({len(names)} fichiers, {copied} sources)", project_id)
    return zip_path
