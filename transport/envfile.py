#!/usr/bin/env python3
"""envfile.py — charge state/.env (lignes `export K=V` ou `K=V`) si non posées.

Défense en profondeur : chaque entrypoint (poller/worker/receiver) charge
lui-même le fichier d'env. Plus aucun mode de lancement ne peut oublier
GH_TRANSPORT_TOKEN / WEBHOOK_SECRET (start_transport.sh, helper, ssh manuel).

Règles :
  - setdefault : ne surcharge JAMAIS une variable déjà dans l'environnement ;
  - jamais de log/affichage de valeur (présence uniquement) ;
  - fichier absent → no-op silencieux (tests, dev).
"""
import os
import pathlib

DEFAULT_ENV_FILE = pathlib.Path(__file__).resolve().parent / "state" / ".env"


def load(env_file=None):
    """Charge le fichier .env. Retourne le dict des clés nouvellement posées."""
    path = pathlib.Path(env_file) if env_file else DEFAULT_ENV_FILE
    loaded = {}
    if not path.exists():
        return loaded
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = True
    return loaded
