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

Note : sur un plan GitHub gratuit, les repos **privés** n'ont pas de branch-protection/rulesets
(API 403) — `required` n'est techniquement possible que sur repo public ou plan Pro. Le mapping
actuel (PASS→success, BLOCK→failure, technique→error) est déjà celui attendu d'un required check.
