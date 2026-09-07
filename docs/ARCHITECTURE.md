# Architecture — Reviewer de PR indépendant Freebuff/Muse Spark

> **MISE À JOUR 2026-09-07 (durcissement + observabilité)** : architecture = **poller GitHub (45 s,
> READ-ONLY) + queue SQLite + worker → fb-vps → commit status « Muse Semantic Review »**, advisory
> réel sur `Revens2/agent-island`. Composants ajoutés : `docker_guard.py` (boundary docker), wrapper
> ROOT `pr-reviewer-docker` (worker `prreview` sans groupe docker), `observe.py`/`report.py`/
> `readiness.py`/`feedback.py` (métriques advisory). Preuves : E2E A/B/C + PR #1 réelle (BLOCK
> certifié 7 findings) — `docs/RAPPORT-FINAL-20260907.md`, `docs/HARDENING.md`,
> `docs/ADVISORY-METRICS.md`. Le receiver webhook ci-dessous reste disponible (non utilisé).

Date : 2026-09-06 (base POC). Statut courant : **ADVISORY PRODUCTION RUNNING, durci** — voir les
mise à jour ci-dessus pour l'état live.

## Vue d'ensemble

```
PR GitHub (webhook, plus tard)
   → Orchestrateur (hôte, hors sandbox ; détient le token GitHub)
       ├─ snapshot read-only : checkout du head SHA (JAMAIS le runner)
       ├─ job dir + lock (concurrency=1)
       └─ docker run --rm --read-only (conteneur jetable durci)
            ├─ /reviewer           instructions TRUSTED (RO, image)
            │   ├─ AGENTS.md       contrat reviewer
            │   └─ .agents/skills/pr-review/SKILL.md
            ├─ /reviewer/pr        snapshot PR (RO)
            ├─ /reviewer/out       résultats (volume dédié)
            ├─ ~/.config/manicode  auth Freebuff + settings + chats (volume persistant)
            └─ tmux → TUI freebuff → prompt review → verdict
   → model_probe (certification modèle : settings + log.jsonl agent)
   → assemble_verdict.py (contrat JSON + politique sévérité/SHA/certification)
   → GitHub Checks (PASS/BLOCK/…)
```

## Primitives Freebuff v0.0.171 (vérifiées le 2026-09-06)

| Élément | Fait constaté |
|---|---|
| CLI | TUI pur (login, --cwd, --continue) ; pas de headless (issue #947 ouverte) |
| Binaire | launcher npm → `~/.config/manicode/freebuff` (linux/arm64 OK, épinglé 0.0.171) |
| Auth | `~/.config/manicode/credentials.json` ; **persiste entre conteneurs** (testé) |
| Modèle | `settings.json` `freebuffModel` ; valeurs `meta/muse-spark-1.3-contributor`, `z-ai/glm-5.3-flash`, `openai/gpt-5.6-luna`… |
| Quota | Freebucks : 100/j ; débit au démarrage de session (Muse ≈ 15/h, GLM 5, GPT-5.6 20) |
| Contrainte | **Une seule instance freebuff par compte** (écran « Take over » si Desktop actif) |
| Fallback | UI : « Muse Spark 1.3 · Queues, then falls back » → fallback possible (DeepSeek V4 Flash listé) |
| Artefacts | `projects/<cwd>/chats/<ts>/{chat-messages.json, run-state.json, log.jsonl}` |

## Certification modèle (Phase 3)

Primitive retenue : `log.jsonl` du chat → lignes `Start/End agent base3-free-muse-spark-1-3 step N`.
L'agent provisionné par le serveur est nommé d'après le modèle admis (vérifié : session Luna →
`base3-free-luna`, session Muse → `base3-free-muse-spark-1-3`). Croisée avec
`settings.freebuffModel` (sélection) et le header TUI (session).

- `MUSE_OK` + certified : settings cible ET agent `*-muse-spark-1-3`.
- agent autre → `MUSE_FALLBACK` / non certifié (jamais de PASS silencieux).
- rien d'exploitable → `MODEL_UNVERIFIED` / non certifié.

Limite documentée : la preuve est au niveau session/étape (agent log), pas une garantie
par-requête du moteur interne ; un fallback intra-session n'a pas pu être observé (pool non saturé).

## Choix d'intégration (P5 — exécuté, décision mesurée)

**PR-Agent v0.45.0 évalué puis non retenu** — pas un abandon de principe : aucune primitive de
délégation externe n'existe. Son webhook/serve (`pr_agent/servers/github_app.py`) route les events
vers ses actions `review`/`describe`/… qui passent toutes par ses providers LiteLLM ; il n'existe
ni hook « reviewer externe », ni publication d'un check produit hors de son moteur IA. Relier notre
worker Freebuff/Muse certifié exigerait soit (A) un fork des providers (couplage aux internals
LiteLLM, interdit par la politique modèle), soit (B) un shim OpenAI-compatible qui perdrait notre
contrat de verdict exact (états fail-closed, certification, anti-spam, stale). Les invariants §6–11
de la mission (queue SQLite concurrency=1, dédup repo+PR+SHA, anti-stale par re-lecture du head,
checks par SHA, remplacement de commentaire, retry INSTANCE_BUSY) sont notre couche transport de
toute façon. Image vérifiée : `pragent/pr-agent:0.45.0-github_app` (linux/arm64, digest
`sha256:2cfd644b0641…`), à réévaluer si upstream ajoute une primitive de délégation externe.

**Transport retenu — orchestrateur maison minimal** (`transport/`) : receiver webhook (HMAC-SHA256,
allowlist events/repos/drafts, corps borné) → file SQLite (id immuable repo|PR|SHA, états
pending/running/retry/done/error, retries bornés) → worker concurrency=1 pilotant le conteneur
`fb-vps` existant (réutilisation de session chaude, boot déterministe par `settings.freebuffModel`,
reprise auto après dépossession « took over », jamais de Take over automatique) → probe de
certification multi-preuves → anti-stale → publication Check (« Muse Semantic Review » :
PASS→success, BLOCK→failure, non certifié→neutral) + commentaire BLOCK remplaçable (marqueur
`<!-- muse-semantic-review -->`). Le token GitHub (PAT aujourd'hui, GitHub App ensuite) vit dans
l'orchestrateur (`transport/state/.env`, chmod 600), jamais dans le conteneur Freebuff. Le worker
Freebuff/Muse validé n'a pas été modifié.
