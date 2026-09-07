# Opérations — pr-reviewer (service long-running, mode advisory réel)

Service VPS : deux unités systemd (`pr-reviewer-poller`, `pr-reviewer-worker`) sous l'utilisateur
`prreview` (**sans** groupe docker — docker piloté via le wrapper ROOT `pr-reviewer-docker`, voir
`docs/HARDENING.md`). L'entrypoint charge `state/.env` lui-même (défense en profondeur).

**Multi-repo (allowlist)** : les dépôts surveillés sont définis par `repos` dans
`transport/config.json` — configuration hors code, versionnable, aucun secret. Le poller scanne
chaque repo de l'allowlist avec les mêmes gates (same-repo, non-draft, auteur autorisé) ; les
jobs sont séparés par identité immuable `repo|PR|head_sha`. Ajouter/retirer un repo = une ligne
de config + restart du poller (aucun changement de code). `health.py` vérifie **chaque** repo de
l'allowlist (`github.monitored`, `github.ok`, `github.failures`) ; `report.py` agrège globalement
et par repo. `Revens2/agent-island` ne fait plus partie de la cible (fixture E2E historique —
ses jobs `done` restent en historique SQLite, aucune logique métier ne le référence).

## Références opératoires

| Action | Commande |
|---|---|
| Démarrer | `sudo systemctl start pr-reviewer-poller pr-reviewer-worker` |
| Arrêter (rollback) | `sudo systemctl stop pr-reviewer-poller pr-reviewer-worker` |
| Redémarrer | `sudo systemctl restart pr-reviewer-poller pr-reviewer-worker` |
| Statut | `systemctl status pr-reviewer-poller pr-reviewer-worker` ; `systemctl is-active pr-reviewer-*` |
| Logs | `journalctl -u pr-reviewer-worker -f` ; `journalctl -u pr-reviewer-poller -n 100` |
| Santé | `python3 transport/health.py` (JSON : poller, worker, queue, github, fb-vps, disque — exit 0/1) |
| Rapport advisory | `python3 transport/report.py --since 7d` (read-only, aucun quota) |
| Rapport par repo | `python3 transport/report.py --since 30d` (global) ; `python3 transport/report.py --repo owner/name --since 30d` (un seul repo) |
| Modifier les repos surveillés | éditer la liste `repos` (allowlist) dans `transport/config.json` puis `sudo systemctl restart pr-reviewer-poller pr-reviewer-worker` |
| Required-readiness | `python3 transport/readiness.py` (NOT_ENOUGH_DATA/NOT_READY/CANDIDATE_READY) |
| Label un finding | `python3 transport/feedback.py label <job_id> <idx> <confirmed|false_positive|unclear|obsolete|duplicate|not_reviewed> [note] [--sev …] [--conf …] [--ev "preuve"]` |
| Inspecter la file | `python3 -c "import sqlite3;print(sqlite3.connect('transport/state/jobs.db').execute('select state,count(*) from jobs group by state').fetchall())"` |
| Relancer un job | `python3 -c "import sqlite3,time;c=sqlite3.connect('transport/state/jobs.db');c.execute(\"update jobs set state='pending',next_run=0 where id='<id>'\");c.commit()"` |
| Purger jobs terminés | `python3 -c "import sqlite3;c=sqlite3.connect('transport/state/jobs.db');print(c.execute(\"delete from jobs where state in ('done','error')\").rowcount);c.commit()"` |

Les fichiers d'état vivent sous `transport/state/` (propriété `prreview:prreview`, 2775/664 —
l'opérateur `juliann` y accède via le groupe `prreview`) : `jobs.db` (file SQLite), `jobs/<jid>/`
(verdicts, preuves par review), `*.heartbeat` (liveness), `feedback.jsonl` (labels humains),
`balance.json` (solde Muse observé). Aucun secret dans ces fichiers de métriques.

## Déploiement / màj de code

1. `git pull` dans le checkout de production (`transport/` = racine du service).
2. `python3 -m py_compile transport/*.py` puis `sudo systemctl restart pr-reviewer-poller
   pr-reviewer-worker`.
3. `python3 transport/health.py` → `"status": "ok"`.

La réconciliation au démarrage du worker remet en file les jobs `running` orphelins (crash) dont le
head est toujours actuel, et marque `STALE_SHA` les autres — aucune queue bloquée après un restart.

## Journal des pannes connues (fail-closed)

| État | Signification | Commit status |
|---|---|---|
| `INSTANCE_BUSY` | une autre instance Freebuff tient le compte (retry borné) | error après épuisement |
| `TIMEOUT` / `TIMEOUT_SESSION` / `TIMEOUT_BOOT` | session Muse injoignable (retry borné) | error après épuisement |
| `AUTH_REQUIRED` | reconnexion Freebuff nécessaire (login URL) | error |
| `FREEBUCKS_EXHAUSTED` | quota épuisé — aucune nouvelle session démarrée | error |
| `MODEL_UNVERIFIED` / non certifié | fallback ou probe KO — jamais de PASS | error |
| `STALE_SHA` | head changé pendant la review | (ancien SHA, rien publié) |
| `INTERNAL_ERROR` | exception worker | error |

`FREEBUCKS_LOW` (solde ≤ `freebucks_low`, défaut 10) est loggé sans bloquer. Le solde est observé
sur l'UI TUI (jamais interrogé ailleurs) et écrit dans `state/balance.json`.

## Rollback

1. **Arrêt fonctionnel du reviewer** : `sudo systemctl stop pr-reviewer-poller
   pr-reviewer-worker` — plus aucune review, aucune PR touchée, aucun repo/ruleset/Action/service
   modifié. Les commentaires/status déjà publiés restent (historiques). Aucun credential supprimé.
2. **Rollback complet du durcissement** (retour aux unités `User=juliann Group=docker`) :
   `deploy/rollback_juliann.sh` en root (helper docker-nsenter) — restaure les unités depuis
   `deploy/backup-units-juliann/` et retire `sudoers.d/pr-reviewer`. Drill exécuté et validé le
   2026-09-07 (rollback → re-durcissement, service resté vert).
3. Redémarrage après màj de code : `sudo systemctl restart pr-reviewer-poller pr-reviewer-worker`
   puis `python3 transport/health.py` → `"status": "ok"`.

## Journal des pannes connues (suite)

- `ENV_LEAK` (incident 2026-09-07, corrigé) : lignes `export K=V` dans l'EnvironmentFile systemd
  rejetées ET journalisées en clair (fuite locale). Fix : `state/systemd.env` sans préfixe
  `export` (sed), chmod 600, purge journal (`journalctl --rotate` + `--vacuum-time=1s`),
  vérification 0 occurrence.
- Durcissement : wrapper refuse toute commande docker hors `start`/`exec`/`inspect-running` sur
  `fb-vps` (exit 3) — validé en prod (DENY `rm fb-vps`).
