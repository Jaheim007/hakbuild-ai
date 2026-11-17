# HakBuild AI — MVP plateforme (stdlib)

Orchestrateur qui transforme une idée décrite en français en application mobile
React Native / Expo : PRD, tickets, scaffolds, backend Convex spécifié, tests,
scan de sécurité et livrable ZIP. Aucune dépendance pip — Python 3.14 + SQLite.

## Commandes

```bash
python scripts/init_db.py                                  # migrations
python -m hakbuild.cli new "BarberBook" "reservation + paiement" --platforms both
python -m hakbuild.cli list
python -m hakbuild.cli advance prj_xxxx                    # s'arrête sur la validation PRD
python -m hakbuild.cli approve prj_xxxx                    # human gate
python -m hakbuild.cli advance prj_xxxx                    # va jusqu'au build + bundle
python -m hakbuild.cli bundle prj_xxxx                     # régénère le livrable
python -m hakbuild.cli verify prj_xxxx --install            # bundle Expo Web + tsc réels
python -m hakbuild.cli scan prj_xxxx                       # sécurité + checks
python -m hakbuild.cli status                              # base, secrets, toolchain
python run_tests.py                                        # 13 tests
python -m hakbuild.cli serve --port 8080                   # dashboard web
```

## Pipeline (machine à états du PRD §13)

`PENDING → ANALYZING → AWAITING_APPROVAL → PLANNING → DESIGNING → DEVELOPING →
CONFIGURING_BACKEND → INTEGRATING → TESTING → SECURITY_SCANNING → BUILDING → COMPLETED`

Branches : `FIXING` (auto-correction, 3 tentatives), `BLOCKED` (intervention
humaine), `FAILED`, `CANCELLED`, retour `COMPLETED → DEVELOPING` pour les
modifications. Chaque transition est journalisée dans `audit_logs`.

## Ce qui est réellement produit

| Étape | Livrable vérifiable |
|---|---|
| ANALYZING | `prd.md` + enregistrement `prd_documents` + demande d'approbation |
| PLANNING | 9 tickets T01–T09 avec définition de terminé |
| DESIGNING | `design/screens.md`, `constants/theme.ts` |
| DEVELOPING | 20 fichiers Expo : `app/*.tsx`, `package.json`, `app.json`, `tsconfig.json` |
| CONFIGURING_BACKEND | `convex/schema.ts`, `convex/<table>.ts` gardés par `getUserIdentity`, schéma plateforme 21 tables |
| INTEGRATING | `lib/convex.ts`, `.env.example`, état des intégrations |
| TESTING | 10 checks statiques enregistrés dans `test_results` |
| SECURITY_SCANNING | `security_reports` (secrets, eval, permissions, fonctions non gardées) |
| BUILDING | build enregistré (`planned` en dry-run, `succeeded`/`failed` en mode execute) |
| COMPLETED | ZIP vérifié : PRD, source, rapports, inventaire SHA256 |

## Preuve de compilation

`python -m hakbuild.cli verify <id> --install` (ou bouton « Verifier le build web »
du dashboard) lance réellement `npx expo export --platform web` puis `npx tsc --noEmit` :

```
ok: True | routes: index, create, profile, details/[id], +not-found
bundle: entry-*.js (1.1 MB) | 0 erreur TS applicative
```

Sans deployment Convex, `convex/_generated` n'existe pas : les seules erreurs `tsc`
sont dans `convex/*.ts` et disparaissent après `npx convex dev`. L'application
elle-même (app/, lib/, constants/) n'en produit aucune.

## APK (EAS Build)

```bash
python -m hakbuild.cli preflight <id>          # liste exacte de ce qui bloque
python -m hakbuild.cli apk <id>                # prepare (git + eas init) sans compiler
python -m hakbuild.cli apk <id> --execute      # lance EAS Build reel
```

`preflight` contrôle : `package.json`, `eas.json`, slug, package Android, `node_modules`,
dépôt git avec au moins un commit, `EXPO_TOKEN`, projet EAS lié (`eas init`).

Le jeton se met dans `.env` (`EXPO_TOKEN=...`, créé sur expo.dev/settings/access-tokens)
et n'est jamais passé en argument de commande ni affiché : `runner.run(env=...)` injecte
l'environnement du processus enfant. Sans jeton, `apk --execute` répond `blocked` avec
la liste des blocages — aucun succès simulé.

## Modes de build

- `BUILD_MODE=dry-run` (défaut) : le plan de commandes est enregistré, aucun APK n'est
  déclaré. Aucun résultat inventé.
- `BUILD_MODE=execute` : `npm install` puis `npx eas-cli build -p android --profile preview`
  sont lancés réellement ; l'échec est enregistré et le projet passe en `FIXING`
  ou `BLOCKED`.

## Sécurité

- Secrets jamais en base ni dans le code : lus depuis `.env` / variables d'environnement.
- Le scan bloque la livraison sur constat `critical` (clé privée, clé OpenRouter,
  fonction Convex sans vérification d'identité).
- Les variables serveur lues depuis `app/`, `lib/`, `components/`, `constants/`
  sont signalées : seul `EXPO_PUBLIC_*` doit atteindre le bundle mobile.

## Deux niveaux Convex (PRD §8)

1. Plateforme : `data/platform/convex/schema.ts` (21 tables du §11).
2. Application : `convex/` du workspace de chaque projet, projet Convex dédié.

## Limites connues

- La compilation APK réelle exige un compte Expo/EAS (`EAS_TOKEN`) ; le bundling web
  fonctionne sans compte (vérifié : 5 routes, 1.1 MB).
- Les captures d'écran et les tests Maestro nécessitent un émulateur ou un appareil.
- La création automatique de projets Convex via Platform APIs n'est pas branchée :
  seuls les fichiers de schéma/fonctions sont générés (`npx convex dev` requis).
- Le PRD est déterministe (heuristiques) ; l'enrichissement par OpenRouter reste à brancher.
- Pas de publication store (action humaine par conception).
