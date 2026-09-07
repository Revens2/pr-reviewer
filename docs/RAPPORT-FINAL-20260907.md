# RAPPORT FINAL — Transport GitHub du reviewer Muse (2026-09-07)

Architecture retenue : **polling GitHub + commit status** — aucune GitHub App, aucun webhook public,
aucun runner Actions, aucun credential supplémentaire.

## Contexte de la mission

Auditer toutes les primitives GitHub existantes (PC + VPS) et relier :

```
git push → GitHub → déclencheur (poller VPS) → reviewer Freebuff/Muse (fb-vps, validé)
         → verdict certifié → commit status « Muse Semantic Review » visible dans la PR
```

Le worker, la queue SQLite, l'anti-stale et les verdicts étaient déjà validés — non refaits.

## Rapport §26

| Élément | Constat live |
|---|---|
| **GitHub MCP PC** | `@modelcontextprotocol/server-github` v0.6.2 **archivé/deprecated**, démarré **sans token** (configs Claude/Codex/`.mcp.json`) → anonyme, **26 outils mais aucun status/check**, incapable de lire un repo privé. Non retenu. |
| **PC gh** | v2.96.0, authentifié **Revens2** (classic `repo` scope). Lecture/écriture complètes sur agent-island. Usage admin/dev uniquement. |
| **VPS gh / PAT** | `gh` présent ; fine-grained token **limité aux repos publics** (404 sur agent-island privé). |
| **VPS token orchestrateur** | `GH_TRANSPORT_TOKEN` (classic `repo`) dans `transport/state/.env` (600) — **seul credential couvrant agent-island**, déjà utilisé par le transport validé. Réutilisé tel quel. |
| **VPS git push** | Non nécessaire : le transport lit via API REST (tarball + compare). Aucun push depuis le VPS. |
| **Actions runners** | Aucun runner self-hosted sur agent-island (CI = windows-latest). Runners Watchy présents sur le VPS mais pour d'autres repos ; GitHub facture désormais les minutes self-hosted sur repos privés → option Actions abandonnée (documentée). |
| **GITHUB_TOKEN Actions** | Validé expérimentalement (PR fixture, workflow jetable GitHub-hosted) : JWT éphémère 3600 s, scope repo, `contents: read` OK, `/user`→403, `pull-requests`→403 sans permission. Confirme : utile dans un workflow, jamais un credential permanent VPS. |
| **Actions transport** | Non retenu (pas de runner + coût minute self-hosted privé 2026 + maintenance runner). |
| **Actions check PASS/BLOCK** | Non testé (runner inexistant) — documenté comme alternative future. |
| **commit status** | **Validé live** : `POST /repos/{r}/statuses/{sha}` → 201 avec le token classic. Affiche « Muse Semantic Review » comme check dans la PR. |
| **Checks API** | **403 confirmé** pour PAT (« You must authenticate via a GitHub App ») — sans importance : commit status suffit. |
| **GitHub App nécessaire ?** | **NON** — chantier GitHub App + webhook public abandonné. |
| **Architecture retenue** | **B** : poller GitHub (45 s, READ-ONLY, mêmes credentials orchestrateur) → queue SQLite existante → worker fb-vps → commit status + commentaire BLOCK. |
| **Raison** | 0 credential nouveau, 0 exposition Internet, 0 composant à maintenir en plus du transport validé, latence 45 s acceptable, commit status utilisable en required status check futur. |
| **Credentials supplémentaires créés** | **Aucun.** |
| **Surface Internet supplémentaire** | **Aucune** (poller sortant uniquement). |

## Preuve E2E A/B/C (agent-island, PR #6 — branche `test/pr-reviewer-…`)

Aucune injection manuelle : uniquement `git push` sur la branche fixture.

| Leg | Commit | Déclenchement | Verdict Muse (certifié) | Commit status GitHub | Heure (UTC) |
|---|---|---|---|---|---|
| **A** clean fixture | `75ea8e3` | poller → job | **PASS**, 0 finding | `success` | 07:46:12 |
| **B** bug logique (`normalize_names` : trim retiré, doublons inversés) | `1de027a` | poller → job | **BLOCK**, 1 finding | `failure` | 07:48:18 |
| **C** correction | `7675694` | poller → job | **PASS**, 0 finding | `success` | 07:51:35 |

- Commentaire BLOCK publié sur la PR #6 (07:48:20Z) — endpoint issues, marqueur anti-spam `<!-- muse-semantic-review -->`.
- Verdicts certifiés : `model_verified = meta/muse-spark-1.3-contributor`, `model_certified = true` (les 3).
- Anti-stale : `head_sha` re-vérifié avant publication (3 SHA distincts, aucun verdict croisé).
- Preuves locales : `results/e2e-abc/leg-{A,B,C}.txt` (verdict + probe + prompt), `results/e2e-pr6-block-comment.md`.

## Correctifs appliqués au transport (bugs réels trouvés en E2E)

1. **Env par entrypoint** : poller/worker/receiver chargent eux-mêmes `state/.env` (`transport/envfile.py`,
   `setdefault` — ne surcharge jamais). Un worker relancé hors `start_transport.sh` n'avait plus
   `GH_TRANSPORT_TOKEN` → job `INTERNAL_ERROR`.
2. **Dédup des jobs en `error`** : `db.enqueue` plantait en `UNIQUE constraint failed` sur une ligne
   terminale `error` → le poller bouclait en erreur à chaque scan. Désormais tout état existant = dédup.
3. **Écran « Session ended »** (survient après CHAQUE review Muse) : `ensure_session` ne le gérait pas →
   `TIMEOUT_SESSION` fail-closed à 120 s. Enter ajouté pour ouvrir une nouvelle session (log
   « session Muse terminée — nouvelle session »).
4. **Doublons de log** : chaque ligne écrite 2× (stdout redirigé + append explicite) → append supprimé.
5. `start_transport.sh` : garde-fou fatal si `GH_TRANSPORT_TOKEN` absent.

Tests offline : **17/17** (`tests/transport_test.py` : signature, dédup, retry borné, mapping verdict,
poller fork/draft/prefix, + régressions error-row et envfile).

## État final

- Stack VPS saine : poller + worker + receiver up, token présent, file vide (3 jobs `done`), scans
  propres (`created 0, dedup 0`), PR réelle #1 (`feat/…`) ignorée par le gate de préfixe pilote.
- Cleanup fait : PR #6 close (+ commentaire), branches fixture + token-probe supprimées (remote/local),
  worktree supprimé. Aucun credential préexistant touché.
- `agent-island` : repo inchangé (aucun fichier du produit modifié, `main` intact).

## Conditions avant advisory réel

1. **Quota Muse** : session terminée avec ~30 Freebucks — recharger avant usage réel soutenu.
2. **Gate de préfixe** : `pr_branch_prefixes: ["test/pr-reviewer-"]` à vider (ou cibler les branches
   voulues) pour reviewer les PR réelles. Gate §11 (same-repo uniquement) déjà actif dans le poller.
3. Décision advisory vs bloquant : aucune protection de branche aujourd'hui → le status est informatif ;
   pour du bloquant, configurer un required status check « Muse Semantic Review » sur la branche cible.
4. Latence : 45 s de polling (vs webhook push) — assumé ; réduire `poll_interval_s` si besoin.

## Verdict

**READY_FOR_ADVISORY_REAL_REPO** — la chaîne complète push → déclenchement auto → reviewer VPS → Muse
certifié → PASS/BLOCK visible dans la PR est prouvée sans clic manuel, sans webhook injecté, sans token
copié dans Freebuff, sans GitHub App, sans exposition Internet.
