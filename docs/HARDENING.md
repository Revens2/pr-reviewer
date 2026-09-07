# Durcissement 2026-09-07 — boundary Docker sans privilège host

État : **appliqué et validé en production** (VPS Étude, Ubuntu 24.04 ARM64).

## Problème traité

Avant durcissement, `pr-reviewer-poller.service` et `pr-reviewer-worker.service` tournaient sous
`juliann`, membre du groupe **docker** (daemon rootful ≈ root host). Le conteneur Freebuff est
correctement sandboxé, mais l'accès docker du service host était le principal risque
infrastructure résiduel.

## Architecture retenue (Option B — launcher privilégié minimal)

Comparée à l'Option A (daemon Docker rootless dédié) : non retenue ici car elle introduirait un
second runtime (rootless, cgroups, stockage, service user systemd) sur un VPS ARM64 déjà chargé —
complexité et risque de régression disproportionnés par rapport au gain (le conteneur `fb-vps`
reste le même, rootless n'aurait pas réduit la surface du wrapper lui-même).

| Couche | Détail |
|---|---|
| Utilisateur service | `prreview` (système, `nologin`, **sans** groupe docker, sans home) |
| Opérations docker autorisées | **3 verbes seulement**, conteneur FIXE `fb-vps` : `start`, `exec -i -u reviewer fb-vps <cmd>`, `inspect -f {{.State.Running}} fb-vps` |
| Wrapper root | `/usr/local/sbin/pr-reviewer-docker` (python3, `root:root` 0755) — **non modifiable** par le worker |
| Guard versionné | `/usr/local/lib/pr_reviewer_docker_guard.py` = copie installée de `transport/docker_guard.py` (root:root 0644) |
| Élévation | `sudoers.d/pr-reviewer` : `prreview` ET `juliann` → `NOPASSWD: /usr/local/sbin/pr-reviewer-docker` (chemin EXACT, aucune autre commande) |
| Unités | `User=prreview Group=prreview` ; poller `NoNewPrivileges=true` (aucun sudo) ; worker **sans** NNP (sudo→wrapper nécessaire) |
| Permissions fichiers | `/home/juliann` `o+x` (traverse), runtime `transport/state/` + `fixtures/input/` → `prreview:prreview` (2775/664, `.env` 660) ; `juliann` ajouté au groupe `prreview` conserve l'accès ops |

`docker_guard.py` n'accepte aucune commande libre : verbe inconnu → refus (exit 3), aucun argument
option docker possible (conteneur/identité littéraux, premier token de commande interdit de
commencer par `-`), NUL/sauts de ligne interdits. Le worker passe par le wrapper via
`PR_REVIEWER_DOCKER_WRAPPER` (posé par l'unité) : sans cette variable (environnement de test /
utilisateur docker group), il utilise la CLI docker directe — comportement historique préservé.

Chaîne d'élévation bornée : `prreview` → `sudo -n` (règle sudoers EXACTE) → wrapper root → guard →
`docker … fb-vps` seulement. Un worker compromis peut piloter le conteneur `fb-vps` (sandbox,
user `reviewer`) mais **pas** le daemon docker ni le host.

## Permissions de fichiers (sans ACL)

Le VPS n'a pas `setfacl` (paquet acl absent). Approche chmod + groupe `prreview` :
`chmod o+x /home/juliann` (traverse uniquement), `chown -R prreview:prreview` sur le runtime
mutable, dirs `2775`, fichiers `664`, `.env`/`systemd.env` `660` (jamais world-readable).

## Validation live (09:21 UTC)

- `id prreview` → `uid=986(prreview) gid=979(prreview)` — **aucun** groupe docker ;
- wrapper via `sudo -n` sous `prreview` : `inspect-running` → `true` ; `exec id` →
  `uid=3741(reviewer)` (dans fb-vps) ;
- `sudo -n … rm fb-vps` → **DENIED** (exit 3) ;
- unités actives sous `prreview`, heartbeats frais, `health.py` → `status: ok` (rc 0) ;
- env du worker vérifié (présence seule) : `PR_REVIEWER_DOCKER_WRAPPER=1`, token présent.

## Déploiement / rollback

- Durcir : copier `deploy/units`, `deploy/root-wrapper/`, `deploy/harden_vps.sh` sur le VPS puis
  exécuter `harden_vps.sh` **en root** (via le helper docker-nsenter utilisé pour l'install
  systemd ; voir `docs/OPERATIONS.md`).
- Backup avant durcissement : `deploy/backup-units-juliann/` (unités `User=juliann Group=docker`).
- **Rollback complet** du durcissement : `deploy/rollback_juliann.sh` (root) — restaure les unités
  juliann + groupe docker et retire le fichier sudoers. Le wrapper et l'utilisateur `prreview`
  restent présents mais inutilisés.
- Rollback fonctionnel (arrêt du reviewer) : `sudo systemctl stop pr-reviewer-poller
  pr-reviewer-worker` — ne touche à aucune PR ni service.

## État post-durcissement

Service advisory opérationnel (même comportement qu'avant), worker sans accès docker
root-equivalent, health vert, tests 34/34, CI verte. Le credential GitHub n'a pas changé dans
cette phase (voir la migration fine-grained documentée dans `docs/SECURITY.md` — création
fine-grained = gate UI GitHub, pas d'API).
