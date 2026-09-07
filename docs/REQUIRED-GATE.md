# Passer « Muse Semantic Review » de advisory à required — critères

Le service est **advisory** par conception. Le rendre `required` (protection de branche / status
check obligatoire) n'est pas une décision de code mais de maturité. Critères exigés avant :

- [ ] plusieurs vraies PR observées (review complète réelle, pas de fixture) ;
- [ ] 0 incident de corruption (aucun verdict faux positif grave non compris) ;
- [ ] 0 faux BLOCK grave non compris (revue humaine des BLOCK pendant la période) ;
- [ ] taux d'erreur technique acceptable (jobs `error`/`INTERNAL_ERROR` < seuil convenu) ;
- [ ] recovery reboot validée (restart VPS + réconciliation queue verts) ;
- [ ] consommation Freebucks comprise (reviews/session, coût/jour mesurés sur `metrics/`) ;
- [ ] fallback modèle correctement fail-closed (jamais de PASS non certifié observé) ;
- [ ] service stable plusieurs jours sans intervention.

## Mécanisme de décision (2026-09-07)

- `python3 transport/readiness.py` → `NOT_ENOUGH_DATA` / `NOT_READY` / `CANDIDATE_READY` + raisons
  (ne modifie rien sur GitHub). Données actuelles : **NOT_ENOUGH_DATA** (1 review réelle < floor de
  signal). Seuils provisoires calibrés ensuite sur l'observation — voir `docs/ADVISORY-METRICS.md`.
- Feedback humain des findings : `python3 transport/feedback.py label <job_id> <idx>
  <confirmed|false_positive|unclear|not_reviewed>` (prérequis pour calculer un vrai taux de faux
  BLOCK).

## Compatibilité GitHub (revalidée en live le 2026-09-07)

Sur un plan GitHub gratuit, les repos **privés** n'ont pas de branch-protection ni rulesets
(API 403 vérifié sur `Revens2/agent-island`, toujours privé) — `required` n'est techniquement
possible que si le repo passe **public** ou sur un plan **Pro**. Sur un repo public
(`Revens2/pr-reviewer`), l'API protection répond (404 « Branch not protected ») — configurable.
Le mapping actuel (PASS→success, BLOCK→failure, technique→error) est déjà celui attendu d'un
required check. Rien n'est activé dans cette mission.
