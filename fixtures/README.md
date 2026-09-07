# Fixtures de test

`build_fixtures.py` régénère les cas d'entrée du reviewer (clean / bug-logic / injection /
malicious-skill…) dans `input/` — de petits dépôts git factices + `change.patch` + `context.json`.

Les snapshots générés (`input/`, avec leurs `.git` internes) ne sont **pas versionnés** :
lancer le script pour les recréer avant `tests/test_offline.sh`.

```bash
python3 fixtures/build_fixtures.py
bash tests/test_offline.sh
```
