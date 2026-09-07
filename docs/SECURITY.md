# Sécurité — pr-reviewer

## Menaces & périmètre

- **Code de PR hostile** (injection, malware, exfiltration) : jamais exécuté. Snapshot read-only,
  réseau conteneur minimal, cap_drop, non-root, pas de docker.sock/gh/ssh dans le conteneur.
  Analyse statique uniquement, skill trusted hors du repo analysé (`reviewer/`).
- **Fallback modèle** (Luna/DeepSeek…) : refusé par certification — `model_certified=false` → `error`.
- **Fork externe / draft / auteur non autorisé** : gates côté poller (même repo, `author_association`
  ∈ OWNER/MEMBER/COLLABORATOR, non-draft). Jamais de session Muse pour ces PR.
- **Credential GitHub** : `GH_TRANSPORT_TOKEN` vit dans `transport/state/.env` (chmod 660,
  propriété `prreview:prreview`), lu par l'orchestrateur uniquement, **jamais transmis au
  conteneur Freebuff**. Ne jamais versionner : `state/.env`, `*.pem`, `credentials.json`, `*.db`
  runtime, logs. `.env.example` = noms + valeurs factices.
- **Boundary docker host (durcissement)** : le service tourne sous `prreview` **sans** groupe
  docker ; docker est piloté via `/usr/local/sbin/pr-reviewer-docker` (wrapper ROOT, sudoers
  NOPASSWD chemin exact) qui ne valide que `start`/`exec -i -u reviewer fb-vps <cmd>`/
  `inspect-running` (guard `docker_guard.py` installé root:root dans `/usr/local/lib`). Pas de
  commande docker libre possible. Détails : `docs/HARDENING.md`.

## Credential & secrets

### Endpoints réellement nécessaires (audités dans `github_client.py`/`worker.py`, 2026-09-07)

| Endpoint | Méthode | Permission fine-grained | Appelé par |
|---|---|---|---|
| `/repos/{r}/pulls?state=open` | GET | Pull requests read | poller |
| `/repos/{r}/pulls/{n}` | GET | Pull requests read | worker |
| `/repos/{r}/compare/{base}...{head}` | GET | Contents read | worker (diff) |
| `/repos/{r}/tarball/{sha}` | GET | Contents read | worker (snapshot) |
| `/repos/{r}/statuses/{sha}` | POST | Commit statuses write | worker |
| `/repos/{r}/issues/{n}/comments` | GET/POST | Pull requests read/write | worker (commentaire BLOCK) |
| `/repos/{r}/issues/comments/{id}` | DELETE | Pull requests write | worker (anti-spam) |
| `/repos/{r}` | GET | Metadata read | health |

Pas besoin : Administration, Actions, Checks write, Workflows, Secrets, Deployments, Gists,
Packages, Issues, Contents write. `create_check` (Checks API) existe dans le code mais **n'est
jamais appelé** par le transport commit-status.

### État actuel + migration vers un credential minimal

- Actuel : PAT **classique** préexistant, scopes observés en live (en-tête, valeur jamais
  affichée) : `repo, workflow, gist, read:packages, read:org` — `repo` couvre le besoin mais
  `workflow`/`gist`/`read:packages`/`read:org` sont **superflus** pour pr-reviewer. Ne pas révoquer
  un token partagé sans identifier ses autres consommateurs.
- **Cible** : fine-grained PAT dédié, accès repo `Revens2/agent-island` uniquement, permissions :
  `Metadata: read` (implicite), `Contents: read`, `Pull requests: read+write`, `Commit statuses:
  read+write`. Aucune autre.
- **Gate UI** : GitHub ne permet PAS de créer un fine-grained PAT par API — création manuelle dans
  l'UI (Settings → Developer settings → Fine-grained tokens). Procédure exacte : nom
  `pr-reviewer-transport`, repository access = *Only select repositories* → `Revens2/agent-island`,
  permissions ci-dessus, expiration courte. Dépôt : remplacer la valeur dans
  `transport/state/.env` (`GH_TRANSPORT_TOKEN`) puis `systemctl restart pr-reviewer-worker
  pr-reviewer-poller` + `health.py`. Ne jamais coller le token dans un chat/log.
- Jamais de `cat`/`env` de token dans les procédures ; les vérifications ne montrent que la présence.

## Leçon intégrée (fuite locale)

systemd **refuse les lignes `export K=V`** dans un `EnvironmentFile` ET journalise la ligne ignorée
(valeur visible dans le journal local). Correction :
- `deploy/systemd/*.service` pointent vers `transport/state/systemd.env`, généré SANS préfixe
  `export` (`sed 's/^export //'`, chmod 600) ;
- les entrypoints chargent de toute façon `state/.env` eux-mêmes (`envfile.py`, `setdefault`) —
  le service fonctionne même si l'EnvironmentFile est absent ;
- purge après incident : `journalctl --rotate` puis `journalctl --vacuum-time=1s` (root).

## Publication (repo public)

Avant tout push : Semgrep (hook) + Gitleaks (CI) + tests + recherche ciblée (`grep -rE
"gho_|ghp_|github_pat_|authToken|PRIVATE KEY"` sur le contenu suivi). `results/`, `state/`,
`recon/`, fichiers runtime sont exclus par `.gitignore`.

## Invariants sandbox (rappels)

- Freebuff jamais lancé avec les privilèges du VPS ; conteneur dédié `fb-vps`.
- Le conteneur ne reçoit jamais : docker.sock, `/`, `/home`, clés SSH, tokens GitHub/MCP, `.env`.
- Concurrency = 1 ; verdict jamais stale (head re-vérifié avant publication).
- Le reviewer est ADVISORY : il ne modifie jamais le code, ne merge pas, ne crée pas de ruleset.
