"""Generation du PRD a partir de l'idee de l'utilisateur.

Deterministe (aucune cle API requise). Si OPENROUTER_API_KEY existe, le meme
document peut etre enrichi plus tard ; le PRD reste ici la source verifiable.
"""
from __future__ import annotations

import re

FEATURE_HINTS = {
    "reservation": ["reservations", "agenda", "creneaux", "rendez-vous"],
    "paiement": ["paiement", "mobile money", "carte", "facturation", "abonnement"],
    "livraison": ["livraison", "commande", "panier", "suivi"],
    "education": ["eleves", "notes", "cours", "presence", "scolarite"],
    "sante": ["patients", "consultation", "ordonnance"],
    "messagerie": ["chat", "message", "conversation", "notification"],
    "geo": ["carte", "geolocalisation", "position", "itineraire"],
    "stock": ["stock", "inventaire", "produit", "catalogue"],
    "immobilier": ["bien", "annonce", "location", "visite"],
}

ENTITY_MAP = {
    "reservation": ("bookings", "date, slot, status, customerName"),
    "paiement": ("payments", "amount, currency, provider, status, reference"),
    "livraison": ("orders", "items, total, status, address"),
    "education": ("students", "name, class, grades"),
    "sante": ("appointments", "patientName, doctorName, scheduledAt, notes"),
    "messagerie": ("messages", "threadId, authorId, body, readAt"),
    "geo": ("locations", "label, lat, lng"),
    "stock": ("products", "name, price, stock, imageUrl"),
    "immobilier": ("listings", "title, price, city, description"),
}

DEFAULT_ENTITIES = [
    ("users", "email, displayName, role"),
    ("items", "title, description, ownerId, createdAt"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[.;\n]", text or "")
    return [p.strip(" -•\t") for p in parts if len(p.strip()) > 12]


def detect_domain(idea: str) -> list[str]:
    low = (idea or "").lower()
    return [domain for domain, words in FEATURE_HINTS.items() if any(w in low for w in words)]


def extract_features(idea: str) -> list[str]:
    """Reprend les puces / phrases de l'idee comme fonctionnalites candidates."""
    lines = [l.strip(" -•\t") for l in (idea or "").splitlines() if l.strip()]
    expanded: list[str] = []
    for line in lines:
        parts = re.split(r"\s+[-•]\s+|\s*;\s*", line)
        expanded.extend(p.strip() for p in parts if p.strip())
    bullets = [l for l in expanded if re.match(r"^[-•*\d]", l.strip())]
    pool = bullets or expanded or _sentences(idea)
    features = []
    for item in pool:
        item = re.sub(r"^[-•*\d.)]+\s*", "", item)
        if 3 <= len(item) <= 120:
            features.append(item[0].lower() + item[1:] if item else item)
    features = features[:8]
    if not features:
        features = ["ecran d'accueil", "saisie et enregistrement de donnees", "consultation de la liste"]
    domain = detect_domain(idea)
    for dom in domain:
        for word in FEATURE_HINTS[dom]:
            label = f"module {word}"
            if not any(word in f for f in features):
                features.append(label)
    return features[:10]


def entities_for(idea: str) -> list[tuple[str, str]]:
    domains = detect_domain(idea)
    entities = [ENTITY_MAP[d] for d in domains if d in ENTITY_MAP]
    if not entities:
        entities = list(DEFAULT_ENTITIES)
    names = [e[0] for e in entities]
    if "users" not in names:
        entities.insert(0, ("users", "email, displayName, role"))
    return entities


def generate_prd(project: dict) -> str:
    name = _clean(project.get("name") or "Application")
    idea = (project.get("idea") or "").strip()
    platforms = project.get("platforms") or "android"
    domains = detect_domain(idea)
    features = extract_features(idea)
    entities = entities_for(idea)
    targets = "Android et iOS" if "both" in platforms else ("Android" if "android" in platforms else "iOS")

    feature_lines = "\n".join(f"- {f}" for f in features)
    entity_lines = "\n".join(f"| `{t}` | {c} |" for t, c in entities)
    domain_line = ", ".join(domains) if domains else "generique"

    return f"""# PRD - {name}

**Version :** 1.0
**Cible :** {targets}
**Domaine detecte :** {domain_line}
**Source :** description fournie par l'utilisateur

## 1. Presentation

{name} est une application mobile {targets.lower()} construite a partir de la demande suivante :

> {_clean(idea) or "(aucune description fournie)"}

## 2. Souffrance

L'utilisateur ne dispose pas aujourd'hui d'un outil mobile fiable pour traiter
son besoin : les informations sont dispersees, la saisie est manuelle et le suivi
n'est pas centralise. Le cout et le delai d'une application sur mesure
representent un frein.

## 3. Solution

{name} centralise le parcours dans une application installee sur le telephone,
avec un backend Convex pour les donnees et l'authentification. L'utilisateur
saisit, consulte et met a jour ses donnees sans dependance a un ordinateur.

## 4. Utilisateurs cibles

| Profil | Besoin principal |
|---|---|
| Utilisateur final | Realiser son parcours en quelques ecrans, sans formation |
| Operateur / gerant | Suivre l'activite et corriger les donnees |
| Administrateur | Controler les acces et les permissions |

## 5. Fonctionnalites

{feature_lines}

## 6. Parcours utilisateur

1. Ouverture de l'application et ecran d'accueil.
2. Creation de compte ou connexion.
3. Creation d'un element principal ({entities[-1][0]}).
4. Consultation de la liste et du detail.
5. Mise a jour ou suppression par le proprietaire.
6. Deconnexion.

## 7. Architecture technique

| Couche | Choix |
|---|---|
| Mobile | React Native + Expo Router + TypeScript |
| Backend | Convex (projet dedie a cette application) |
| Authentification | Convex Auth (mot de passe + session) |
| Etat | Hooks React + requetes Convex |
| Compilation | EAS Build (profil preview puis production) |
| Tests | Checks TypeScript, Maestro pour les parcours critiques |

## 8. Besoins en base de donnees

| Table | Champs principaux |
|---|---|
{entity_lines}

Index recommandes : `by_owner` sur chaque table contenant `ownerId`,
`by_creation` sur `_creationTime`.

## 9. Criteres de reussite

- Les fonctionnalites listees a la section 5 sont accessibles dans l'application.
- Un utilisateur peut creer, lire et modifier ses propres donnees.
- Un utilisateur ne peut pas lire les donnees d'un autre utilisateur.
- Aucun secret n'est present dans le bundle mobile.
- Les checks TypeScript passent sans erreur.
- Un APK de test est genere et installable.

## 10. Hors perimetre (MVP)

- Publication automatique sur les stores.
- Paiement reel si non mentionne dans la section 5.
- Fonctionnalites iOS natives non supportees par Expo Go.
"""


def acceptance_criteria(prd_md: str) -> list[str]:
    """Extrait la section 9 du PRD (criteres de reussite)."""
    match = re.search(r"## 9\. Criteres de reussite\n(.*?)(?:\n## |\Z)", prd_md, re.S)
    if not match:
        return []
    return [l.strip("- ").strip() for l in match.group(1).splitlines() if l.strip().startswith("-")]
