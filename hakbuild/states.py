"""Machine a etats des projets (PRD section 13).

Toute transition passe par `transition()` : validation de l'arete, mise a jour
de la base et ecriture dans audit_logs. Aucune transition sauvage possible.
"""
from __future__ import annotations

from .db import audit, now

STATES = [
    "PENDING",
    "ANALYZING",
    "AWAITING_APPROVAL",
    "PLANNING",
    "DESIGNING",
    "DEVELOPING",
    "CONFIGURING_BACKEND",
    "INTEGRATING",
    "TESTING",
    "SECURITY_SCANNING",
    "FIXING",
    "BUILDING",
    "COMPLETED",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
]

LABELS = {
    "PENDING": "En attente",
    "ANALYZING": "Analyse de la demande",
    "AWAITING_APPROVAL": "En attente de validation",
    "PLANNING": "Planification",
    "DESIGNING": "Conception",
    "DEVELOPING": "Developpement",
    "CONFIGURING_BACKEND": "Configuration Convex",
    "INTEGRATING": "Integration des services",
    "TESTING": "Tests",
    "SECURITY_SCANNING": "Analyse de securite",
    "FIXING": "Correction des erreurs",
    "BUILDING": "Compilation",
    "COMPLETED": "Projet termine",
    "FAILED": "Echec",
    "BLOCKED": "Intervention necessaire",
    "CANCELLED": "Projet annule",
}

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"ANALYZING", "CANCELLED"},
    "ANALYZING": {"AWAITING_APPROVAL", "FAILED", "CANCELLED"},
    "AWAITING_APPROVAL": {"PLANNING", "BLOCKED", "CANCELLED"},
    "PLANNING": {"DESIGNING", "FAILED", "CANCELLED"},
    "DESIGNING": {"DEVELOPING", "FAILED", "CANCELLED"},
    "DEVELOPING": {"CONFIGURING_BACKEND", "FAILED", "BLOCKED", "CANCELLED"},
    "CONFIGURING_BACKEND": {"INTEGRATING", "FAILED", "BLOCKED", "CANCELLED"},
    "INTEGRATING": {"TESTING", "FAILED", "BLOCKED", "CANCELLED"},
    "TESTING": {"SECURITY_SCANNING", "FIXING", "FAILED", "CANCELLED"},
    "FIXING": {"TESTING", "DEVELOPING", "FAILED", "BLOCKED", "CANCELLED"},
    "SECURITY_SCANNING": {"FIXING", "BUILDING", "FAILED", "BLOCKED", "CANCELLED"},
    "BUILDING": {"COMPLETED", "FIXING", "FAILED", "BLOCKED", "CANCELLED"},
    "COMPLETED": {"DEVELOPING"},  # demande de modification
    "FAILED": {"PENDING", "ANALYZING"},
    "BLOCKED": {"PLANNING", "DEVELOPING", "CONFIGURING_BACKEND", "BUILDING", "CANCELLED"},
    "CANCELLED": set(),
}

# Etat suivant du pipeline nominal.
NEXT_STEP = {
    "PENDING": "ANALYZING",
    "ANALYZING": "AWAITING_APPROVAL",
    "AWAITING_APPROVAL": "PLANNING",
    "PLANNING": "DESIGNING",
    "DESIGNING": "DEVELOPING",
    "DEVELOPING": "CONFIGURING_BACKEND",
    "CONFIGURING_BACKEND": "INTEGRATING",
    "INTEGRATING": "TESTING",
    "TESTING": "SECURITY_SCANNING",
    "SECURITY_SCANNING": "BUILDING",
    "BUILDING": "COMPLETED",
}


class InvalidTransition(Exception):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition(conn, project_id: str, target: str, actor: str = "agent", reason: str = "") -> str:
    """Applique une transition validee et la journalise. Retourne le nouvel etat."""
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise InvalidTransition(f"projet inconnu: {project_id}")
    current = row["status"]
    if target not in STATES:
        raise InvalidTransition(f"etat inconnu: {target}")
    if not can_transition(current, target):
        raise InvalidTransition(f"transition interdite {current} -> {target}")
    conn.execute(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (target, now(), project_id),
    )
    conn.commit()
    audit(conn, actor, "project.transition", project_id, {"from": current, "to": target, "reason": reason})
    return target
