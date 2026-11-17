"""Plan de taches (tickets) derive du PRD.

Chaque ticket porte une definition de termine verifiable, ce qui permet a
l'orchestrateur de marquer DONE avec une preuve et pas avec une intention.
"""
from __future__ import annotations

from . import prd as prd_mod


def build_tickets(project: dict, prd_md: str) -> list[dict]:
    name = project.get("name") or "Application"
    features = prd_mod.extract_features(project.get("idea") or "")
    entities = prd_mod.entities_for(project.get("idea") or "")
    main_entity = [e for e in entities if e[0] != "users"][-1][0] if len(entities) > 1 else "items"
    criteria = prd_mod.acceptance_criteria(prd_md)
    feature_list = " ; ".join(features[:6]) if features else "ecran d'accueil et liste"

    return [
        {
            "id": "T01",
            "title": "Identite visuelle et ecrans",
            "phase": "DESIGNING",
            "detail": f"Palette, typographie, 4 ecrans ({feature_list}) et etats vide/chargement/erreur.",
            "done": "design/screens.md et constants/theme.ts presents, 4 ecrans decrits",
        },
        {
            "id": "T02",
            "title": "Scaffold React Native / Expo",
            "phase": "DEVELOPING",
            "detail": "package.json, app.json, tsconfig.json, babel.config.js, structure app/.",
            "done": "package.json valide, app.json contient le slug et les plateformes",
        },
        {
            "id": "T03",
            "title": f"Ecrans et navigation ({main_entity})",
            "phase": "DEVELOPING",
            "detail": "Expo Router : layout, liste, creation, detail avec etats de chargement et d'erreur.",
            "done": "app/_layout.tsx, app/index.tsx et app/details/[id].tsx exportent un composant",
        },
        {
            "id": "T04",
            "title": "Schema Convex et index",
            "phase": "CONFIGURING_BACKEND",
            "detail": f"Tables {', '.join(e[0] for e in entities)}, index by_owner et by_creation.",
            "done": "convex/schema.ts declare toutes les tables avec leurs index",
        },
        {
            "id": "T05",
            "title": "Queries, mutations et autorisations",
            "phase": "CONFIGURING_BACKEND",
            "detail": "Fonctions Convex gardees : identite obligatoire, filtrage par ownerId.",
            "done": "chaque fonction verifie ctx.auth.getUserIdentity()",
        },
        {
            "id": "T06",
            "title": "Connexion frontend vers Convex",
            "phase": "INTEGRATING",
            "detail": "Client ConvexReactClient, variables EXPO_PUBLIC_*, aucune cle privee embarquee.",
            "done": "lib/convex.ts lit EXPO_PUBLIC_CONVEX_URL et .env.example ne contient aucun secret",
        },
        {
            "id": "T07",
            "title": "Checks statiques et tests",
            "phase": "TESTING",
            "detail": "Validation JSON, presence des fichiers, coherence des imports, tsc si disponible.",
            "done": "test_results enregistre 0 echec bloquant",
        },
        {
            "id": "T08",
            "title": "Analyse de securite",
            "phase": "SECURITY_SCANNING",
            "detail": "Secrets, cles admin, permissions, acces non authentifies, dependances.",
            "done": "security_reports liste les constats et aucun constat critique non corrige",
        },
        {
            "id": "T09",
            "title": "Compilation et livraison",
            "phase": "BUILDING",
            "detail": "Profil EAS preview, suivi du statut, collecte de l'artefact et du bundle de preuves.",
            "done": "build enregistre avec statut et artefacts verifiables",
        },
    ]


def tickets_for_phase(tickets: list[dict], phase: str) -> list[dict]:
    return [t for t in tickets if t["phase"] == phase]
