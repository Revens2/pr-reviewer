# Opérations — pr-reviewer (service long-running, mode advisory réel)

Service VPS : deux unités systemd (`pr-reviewer-poller`, `pr-reviewer-worker`), exécutées par un
utilisateur non-root membre du groupe docker (pilotage du conteneur `fb-vps` existant — jamais de
nouveau privilège host). L'entrypoint charge `state/.env` lui-même (défense en profondeur).

## Références opératoires

| Action | Commande |
|---|---|
| Démarrer | `sudo systemctl start pr-reviewer-poller pr-reviewer-worker` |
| Arrêter (rollback) | `sudo systemctl stop pr-reviewer-poller pr-reviewer-worker` |
| Redémarrer | `sudo systemctl restart pr-reviewer-poller pr-reviewer-worker` |
| Statut | `systemctl status pr-reviewer-poller pr-reviewer-worker` ; `systemctl is-active pr-reviewer-*` |
| Logs | `journalctl -u pr-reviewer-worker -f` ; `journalctl -u pr-reviewer-poller -n 100` |
| Santé | `python3 transport/health.py` (JSON : poller, worker, queue, github, fb-vps, disque — exit 0/1) |
| Inspecter la file | `python3 -c "import sqlite3;print(sqlite3.connect('transport/state/jobs.db').execute('select state,count(*) from jobs group by state').fetchall())"` |
| Relancer un job | `python3 -c "import sqlite3,time;c=sqlite3.connect('transport/state/jobs.db');c.execute(\"update jobs set state='pending',next_run=0 where id='<id>'\");c.commit()"` |
| Purger jobs terminés | `python3 -c "import sqlite3;c=sqlite3.connect('transport/state/jobs.db');print(c.execute(\"delete from jobs where state in ('done','error')\").rowcount);c.commit()"` |

Les fichiers d'état vivent sous `transport/state/` : `jobs.db` (file SQLite), `jobs/<jid>/`
(verdicts, preuves par review), `*.heartbeat` (liveness), `metrics/*.jsonl` (observabilité),
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

`sudo systemctl stop pr-reviewer-poller pr-reviewer-worker` suffit : plus aucune review, aucune PR
touchée, aucun repo/ruleset/Action/service modifié. Les commentaires/status déjà publiés restent
(historiques). Aucun credential n'est supprimé.
