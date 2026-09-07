#!/usr/bin/env python3
"""docker_guard.py — validation STRICTE des opérations docker autorisées pour le
worker pr-reviewer (utilisée par le wrapper root, PAS de commande libre).

Le worker ne doit avoir aucun accès root-equivalent (plus de groupe docker).
Seul ce wrapper (root, non modifiable par le worker) peut atteindre le daemon, et
uniquement pour le conteneur fixe `fb-vps` avec des flags/identité FIXES :

  docker start fb-vps
  docker exec -i -u reviewer fb-vps <commande…>   (commande dans la sandbox, user reviewer)
  docker inspect -f '{{.State.Running}}' fb-vps    (healthcheck)

Règles de sécurité :
  - verbe non reconnu → refus ;
  - conteneur et user littéraux (jamais passés par l'appelant) ;
  - aucun argument d'option docker possible : tout ce qui suit le conteneur est
    la commande de la sandbox ; premier token ne doit pas commencer par '-' ;
  - interdiction des séparateurs/traversal par le shell : tout argument doit
    être une chaîne simple (pas de NUL, pas de nouvelle ligne).
"""
import shlex


class GuardError(Exception):
    """Appel refusé par la politique du wrapper."""


def docker_argv(verb, args):
    """Retourne la liste argv docker complète pour (verbe, args), ou lève
    GuardError. `args` = liste déjà découpée (pas une chaîne shell)."""
    if verb == "start":
        if args:
            raise GuardError("start ne prend aucun argument")
        return ["docker", "start", "fb-vps"]
    if verb == "inspect-running":
        if args:
            raise GuardError("inspect-running ne prend aucun argument")
        return ["docker", "inspect", "-f", "{{.State.Running}}", "fb-vps"]
    if verb == "exec":
        if not args:
            raise GuardError("exec exige une commande")
        for a in args:
            if "\x00" in a or "\n" in a or "\r" in a:
                raise GuardError("argument invalide (séparateur interdit)")
        first = args[0]
        if first.startswith("-"):
            raise GuardError("option docker interdite en tête de commande")
        # identité/container FIXES — l'appelant ne les fournit jamais
        return ["docker", "exec", "-i", "-u", "reviewer", "fb-vps"] + list(args)
    raise GuardError(f"verbe inconnu: {verb!r}")


def main(argv):
    import sys
    try:
        cmd = docker_argv(argv[1], argv[2:])
    except GuardError as e:
        print(f"pr-reviewer-docker: DENIED: {e}", file=sys.stderr)
        return 3
    import os
    os.execvp(cmd[0], cmd)
    return 0  # jamais atteint


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
