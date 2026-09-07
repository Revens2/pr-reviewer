# pr-reviewer

Independent semantic PR reviewer driven by Freebuff (Muse Spark), running in an isolated
Docker sandbox on your own VPS, publishing advisory verdicts back to GitHub as commit
statuses — **no GitHub App, no webhook, no public endpoint, no new credentials**.

```
git push / PR opened (same repo, non-draft, trusted author)
   → poller (45s, READ-ONLY GitHub API)
   → SQLite queue (dedup repo|PR|head_sha, concurrency=1)
   → worker → snapshot read-only du head + diff
   → Freebuff/Muse dans fb-vps (sandbox docker, session chaude)
   → verdict JSON certifié (modèle vérifié) → commit status « Muse Semantic Review »
   → commentaire PR si BLOCK (1 seul, remplaçable)
```

Statut : **ADVISORY** (jamais `required`, jamais de ruleset). Verdict visible : `success`
(PASS certifié), `failure` (BLOCK certifié), `error` (échec technique / non certifié —
un problème ne devient jamais `success`).

## Multi-repo (explicit allowlist)

The reviewer is repo-agnostic. The set of monitored repositories is an **explicit allowlist**
living in `transport/config.json` on the VPS (template: `transport/config.example.json`) —
outside the code, easy to change, versioned (it holds no secret):

```json
"repos": ["Revens2/pr-reviewer", "Revens2/homelab-ops", "owner/another-active-repo"],
```

- Add/remove a repository = **one config line** + `systemctl restart pr-reviewer-poller`;
  the 45 s poll cycle then applies. No code change, no image rebuild.
- Every repo gets the same gates (draft → SKIP, fork head → SKIP, author association,
  stale-SHA protection) and the same fail-closed model policy.
- Jobs are keyed `repo|PR|head_sha`: the same PR number on two different repositories never
  collides (`poller.py` scans the allowlist, `worker.py` only reads the job row).
- Observability aggregates per repository: `health.py` checks every allowlisted repo,
  `report.py` prints a per-repo breakdown and accepts `--repo owner/name`.

`Revens2/agent-island` is **not** a target of the service: it was the historical end-to-end
fixture used to validate the chain, and its old E2E jobs remain in the SQLite history as
evidence only (no special-case code exists anywhere — see `docs/ARCHITECTURE.md`).

## Déclencheur (couche sémantique)

The deterministic layers (Semgrep, Gitleaks, tests, CI) run in their own pipelines — never here.
This service only adds the **semantic layer**:

- trigger = an open PR (same repo, non-draft, trusted author) on an allowlisted repository,
  or a new `head_sha` pushed to such a PR — polled every 45 s, no webhook;
- a raw push to a branch that belongs to **no** open PR is never reviewed;
- a push to a branch of an open PR triggers a review of the new SHA (dedup `repo|PR|head_sha`,
  stale-SHA protection before any publish).

## Non-négociables

- Le conteneur Freebuff ne peut **jamais modifier le repo** : snapshot monté en lecture
  seule, aucune écriture, aucun push. Il ne reçoit aucun token GitHub ni credential hôte.
- Code d'une PR = données non fiables (anti prompt-injection, skill trusted hors repo).
- **Jamais de PASS sans certification modèle** (Muse vérifié) : fallback non certifié → `error`.
- `Semgrep`/`Gitleaks`/tests = déterministes ; le reviewer Muse = revue sémantique indépendante
  (les deux complémentaires, aucun ne remplace l'autre).
- Forks externes et drafts : jamais reviewés (gates dans le poller).

## Layout

| Chemin | Contenu |
|---|---|
| `transport/` | Orchestrateur : `poller.py`, `worker.py`, `db.py` (queue SQLite), `github_client.py`, `envfile.py`, `docker_guard.py`, `health.py`, `observe.py`/`report.py`/`readiness.py`/`feedback.py` (observabilité advisory), `receiver.py` (webhook optionnel, non utilisé) |
| `reviewer/` | Image Docker sandbox + instructions trusted `AGENTS.md` + skill `pr-review` + scripts orchestrator (driver tmux, probe modèle, assemble verdict) |
| `tests/` | Tests offline (aucun quota modèle) : `transport_test.py` + `hardening_test.py` (43) |
| `deploy/systemd/` | Unités service durcies `pr-reviewer-poller` / `pr-reviewer-worker` |
| `deploy/root-wrapper/` | Wrapper ROOT `pr-reviewer-docker` (boundary docker, voir `docs/HARDENING.md`) |
| `deploy/harden_vps.sh` / `deploy/rollback_juliann.sh` | Durcissement + rollback (root) |
| `fixtures/` | Générateur de fixtures de test |
| `docs/` | ARCHITECTURE, SECURITY, OPERATIONS, HARDENING, ADVISORY-METRICS, REQUIRED-GATE, RAPPORT-FINAL |

## Prérequis (VPS cible)

- Docker (daemon rootful), conteneur `fb-vps` = image `reviewer/` avec une session Freebuff
  authentifiée (volume d'auth).
- **Durcissement (recommandé)** : déploiement `deploy/harden_vps.sh` → service sous utilisateur
  `prreview` **sans** groupe docker, docker piloté via le wrapper ROOT `pr-reviewer-docker`
  (seulement `start`/`exec`/`inspect` sur le conteneur fixe `fb-vps`). Voir `docs/HARDENING.md`.
- Credential GitHub : PAT classique `repo` (lecture PR + statuses + commentaires) ; migration
  fine-grained documentée dans `docs/SECURITY.md`. Jamais dans le repo : voir `.env.example`.

## Démarrage rapide

```bash
cp .env.example transport/state/.env   # renseigner GH_TRANSPORT_TOKEN (+ WEBHOOK_SECRET si receiver)
cp transport/config.example.json transport/config.json   # adapter les chemins/repos
# générer le fichier d'env PLAIN pour systemd (les lignes `export K=V` sont refusées par systemd)
sed 's/^export //' transport/state/.env > transport/state/systemd.env && chmod 600 transport/state/systemd.env

sudo cp deploy/systemd/pr-reviewer-*.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pr-reviewer-poller pr-reviewer-worker

python3 transport/health.py            # santé structurée JSON (jamais de session Muse)
python3 transport/report.py --since 7d # rapport advisory (lecture seule)
python3 transport/readiness.py        # NOT_ENOUGH_DATA / NOT_READY / CANDIDATE_READY
```

Rollback : `sudo systemctl stop pr-reviewer-poller pr-reviewer-worker` — n'interrompt que le
reviewer ; aucune PR, aucun repo, aucun runner ni service applicatif n'est touché. Rollback du
durcissement (retour unités `juliann` + groupe docker) : `deploy/rollback_juliann.sh` (root).

## Tests

```bash
python3 -m unittest tests.transport_test tests.hardening_test  # queue/gates/verdicts/reconcile/quota/guard/readiness/feedback-qualify + multi-repo (43)
bash tests/test_offline.sh                 # driver FAKE_TUI + assemble + adversarial (image locale)
```

CI (`.github/workflows/ci.yml`) : secret scan (Gitleaks) + tests + compile — indépendante du
reviewer Muse (pas d'auto-review obligatoire).

## Licence

MIT — voir `LICENSE`.
