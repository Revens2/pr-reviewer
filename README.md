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
| `transport/` | Orchestrateur : `poller.py`, `worker.py`, `db.py` (queue SQLite), `github_client.py`, `envfile.py`, `health.py`, `receiver.py` (webhook optionnel, non utilisé) |
| `reviewer/` | Image Docker sandbox + instructions trusted `AGENTS.md` + skill `pr-review` + scripts orchestrator (driver tmux, probe modèle, assemble verdict) |
| `tests/` | Tests offline (aucun quota modèle) |
| `deploy/systemd/` | Unités service `pr-reviewer-poller` / `pr-reviewer-worker` |
| `fixtures/` | Générateur de fixtures de test |
| `docs/` | ARCHITECTURE, SECURITY, OPERATIONS, RAPPORT-FINAL, REQUIRED-GATE |

## Prérequis (VPS cible)

- Docker (daemon rootful ou accessible), un compte pour les services (groupe docker).
- Conteneur `fb-vps` = image `reviewer/` avec une session Freebuff authentifiée (volume d'auth).
- Credential GitHub : PAT classique `repo` (lecture PR + statuses + commentaires). Jamais dans
  le repo : voir `.env.example` + `docs/SECURITY.md`.

## Démarrage rapide

```bash
cp .env.example transport/state/.env   # renseigner GH_TRANSPORT_TOKEN (+ WEBHOOK_SECRET si receiver)
cp transport/config.example.json transport/config.json   # adapter les chemins/repos
# générer le fichier d'env PLAIN pour systemd (les lignes `export K=V` sont refusées par systemd)
sed 's/^export //' transport/state/.env > transport/state/systemd.env && chmod 600 transport/state/systemd.env

sudo cp deploy/systemd/pr-reviewer-*.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pr-reviewer-poller pr-reviewer-worker

python3 transport/health.py            # santé structurée JSON (jamais de session Muse)
```

Rollback : `sudo systemctl stop pr-reviewer-poller pr-reviewer-worker` — n'interrompt que le
reviewer ; aucune PR, aucun repo, aucun runner ni service applicatif n'est touché.

## Tests

```bash
python3 -m unittest tests.transport_test   # queue/gates/verdicts/reconcile/quota (25)
bash tests/test_offline.sh                 # driver FAKE_TUI + assemble + adversarial (image locale)
```

CI (`.github/workflows/ci.yml`) : secret scan (Gitleaks) + tests + compile — indépendante du
reviewer Muse (pas d'auto-review obligatoire).

## Licence

MIT — voir `LICENSE`.
