# Sécurité — pr-reviewer

## Menaces & périmètre

- **Code de PR hostile** (injection, malware, exfiltration) : jamais exécuté. Snapshot read-only,
  réseau conteneur minimal, cap_drop, non-root, pas de docker.sock/gh/ssh dans le conteneur.
  Analyse statique uniquement, skill trusted hors du repo analysé (`reviewer/`).
- **Fallback modèle** (Luna/DeepSeek…) : refusé par certification — `model_certified=false` → `error`.
- **Fork externe / draft / auteur non autorisé** : gates côté poller (même repo, `author_association`
  ∈ OWNER/MEMBER/COLLABORATOR, non-draft). Jamais de session Muse pour ces PR.
- **Credential GitHub** : `GH_TRANSPORT_TOKEN` vit dans `transport/state/.env` (chmod 600), lu par
  l'orchestrateur uniquement, **jamais transmis au conteneur Freebuff**. Ne jamais versionner :
  `state/.env`, `*.pem`, `credentials.json`, `*.db` runtime, logs. `.env.example` = noms + valeurs factices.

## Credential & secrets

- PAT classique `repo` (lecture repo/PR/compare, écriture statuses + commentaires issues).
  Périmètre effectivement nécessaire ; scopes `workflow`/`gist`/`read:packages` présents
  historiquement sont **superflus** — recommandation : credential dédié fine-grained
  (Contents read, Commit statuses write, Pull requests read+write) à terme ; ne pas révoquer un
  token partagé sans vérifier ses autres usages.
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
