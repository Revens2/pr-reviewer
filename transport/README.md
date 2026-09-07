# Transport GitHub → reviewer Freebuff/Muse (ADVISORY, multi-repo)

Chaîne automatisée (production, sans webhook ni GitHub App) :

```
PR ouverte / nouveau head_sha sur un repo de l'allowlist
        ↓
poller.py  (toutes les poll_interval_s=45 s, GitHub API READ-ONLY)
        ↓  gates : même repo (fork externe → SKIP), non-draft, author_association autorisée
queue SQLite  (concurrency=1, dedup repo+PR+head_sha, retries bornés)
        ↓
worker.py  (snapshot read-only head SHA → conteneur fb-vps Freebuff/Muse → verdict certifié)
        ↓
anti-stale (re-lecture head SHA GitHub AVANT publication)
        ↓
commit status « Muse Semantic Review » (PASS→success, BLOCK→failure, technique/non certifié→error)
        + commentaire PR si BLOCK (1 seul, remplaçable, marqueur <!-- muse-semantic-review -->)
```

## Multi-repo (allowlist)

- Les dépôts surveillés sont listés dans `repos` (transport/config.json sur le VPS ;
  `config.example.json` = modèle). Config hors code, versionnable, aucun secret.
- Ajouter/retirer un repo = une ligne + `systemctl restart pr-reviewer-poller` (et worker si
  publié) ; le 45 s poll s'applique ensuite. Aucun repo n'est hardcodé dans `poller.py`/`worker.py`.
- Identité de job immuable `repo|PR|head_sha` : un même numéro de PR sur deux repos différents ne
  se mélange jamais.
- Couche sémantique uniquement : un push brut sur une branche SANS PR ouverte n'est jamais
  reviewé ; un push sur la branche d'une PR ouverte → review du nouveau SHA. Les couches
  déterministes (Semgrep/Gitleaks/tests/CI) restent dans leurs pipelines respectifs.

## Déclencheurs & événements

Le poller scanne l'état GitHub (pas de webhook) : `opened`, `reopened`, `ready_for_review` et les
nouveaux `synchronize` sont tous couverts naturellement — chaque SHA inconnu d'une PR ouverte est
enqueue. Le receiver HTTP webhook HMAC (`receiver.py`) reste disponible mais n'est pas utilisé en
production.

## Sécurité

Mode ADVISORY : aucun check required, aucune protection de branche modifiée. Le token GitHub
(PAT ou GitHub App) vit dans l'orchestrateur (`transport/state/.env`, chmod 600), JAMAIS dans le
conteneur Freebuff. Le conteneur garde l'isolation validée (RO, cap_drop ALL, pas de socket
docker, pas de token, pas de code PR exécuté — snapshot lu statiquement uniquement).

Décision PR-Agent : voir `../docs/ARCHITECTURE.md` (évalué v0.45.0, non retenu — aucun point de
délégation externe sans fork des providers LiteLLM).
