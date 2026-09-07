# Transport GitHub → reviewer Freebuff/Muse (ADVISORY)

Chaîne automatisée :

```
GitHub PR event (webhook HMAC vérifié)
        ↓
receiver.py  (HTTP, signature X-Hub-Signature-256 obligatoire)
        ↓
queue SQLite  (concurrency=1, dedup repo+PR+head_sha, retries bornés)
        ↓
worker.py  (snapshot head SHA → conteneur fb-vps Freebuff/Muse → verdict certifié)
        ↓
anti-stale (re-lecture head SHA GitHub AVANT publication)
        ↓
Checks + commentaire GitHub (PASS→success, BLOCK→failure, REVIEW_UNAVAILABLE→neutral/failure)
```

Décision PR-Agent : voir `../docs/ARCHITECTURE.md` (évalué v0.45.0, image `pragent/pr-agent:0.45.0-github_app`
ARM64 digest `sha256:2cfd644b0641…` ; non retenu : aucun point de délégation externe sans fork des
providers LiteLLM ; le transport maison implémente les invariants §6–11 de la mission).

Mode ADVISORY : aucun check required. Aucune protection de branche modifiée.

Sécurité : le token GitHub (PAT ou GitHub App) vit dans l'orchestrateur (`github_token`/env), JAMAIS
dans le conteneur Freebuff. Le conteneur Freebuff garde l'isolation validée (RO, cap_drop ALL, pas de
socket docker, pas de token).
