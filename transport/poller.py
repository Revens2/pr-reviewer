#!/usr/bin/env python3
"""poller.py — déclencheur sans webhook ni GitHub App (architecture retenue 2026-09-07).

Mode ADVISORY RÉEL (2026-09-07) : toutes les `poll_interval_s` secondes, liste les
PR OUVERTES non-draft des repos autorisés et enqueue chaque nouveau head_sha dans
la file SQLite existante (dédup natif repo|pr|head_sha). Gates :
  - même repo uniquement (fork externe → SKIP, jamais de review) ;
  - author_association autorisée (OWNER/MEMBER/COLLABORATOR par défaut) ;
  - draft → SKIP (sauf repo explicitement dans draft_repos pour fixtures) ;
  - préfixe de branche optionnel (pr_branch_prefixes vide = toutes les branches).

Le worker validé fait le reste (snapshot → Muse → verdict → commit status +
commentaire BLOCK). Aucun credential supplémentaire : token orchestrateur du
transport (GH_TRANSPORT_TOKEN, state/.env). READ-ONLY sur GitHub côté poller.
"""
import json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import envfile
envfile.load()
import db as qdb
import github_client as gh

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent
METRICS_DIR = STATE / "metrics"
METRICS_DIR.mkdir(parents=True, exist_ok=True)
INTERVAL = float(CFG.get("poll_interval_s", 45))
REPOS = CFG.get("repos", [])
DRAFT_REPOS = set(CFG.get("draft_repos", []))
# Vide (défaut advisory réel) = toutes les branches same-repo ; préfixe = mode pilote.
BRANCH_PREFIXES = [p for p in CFG.get("pr_branch_prefixes", []) if p]
AUTHOR_ASSOCIATIONS = set(CFG.get("author_associations",
                                  ["OWNER", "MEMBER", "COLLABORATOR"]))


def log(msg, **kv):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}" + \
           ((" " + json.dumps(kv, ensure_ascii=False)) if kv else "")
    print(line, flush=True)  # journald (systemd) ou state/logs (start_transport.sh)


def scan_once(con):
    """Retourne (created, dedup, forks, drafts, authors, prefix, errors)."""
    created = dedup = forks = drafts = authors = prefix = errors = 0
    for repo in REPOS:
        try:
            pulls = gh.list_open_pulls(repo)
        except RuntimeError as e:
            errors += 1
            log("list pulls failed", repo=repo, err=str(e)[-200:])
            continue
        for pr in pulls:
            head = pr.get("head") or {}
            number = pr.get("number")
            head_ref = head.get("ref") or ""
            if BRANCH_PREFIXES and not any(head_ref.startswith(p) for p in BRANCH_PREFIXES):
                prefix += 1
                continue
            if not number:
                continue
            head_repo = (head.get("repo") or {}).get("full_name")
            # Gate §11 : PR du même repo uniquement (fork → jamais de review).
            if head_repo and head_repo != repo:
                forks += 1
                log("fork skipped", repo=repo, pr=number, head_repo=head_repo)
                continue
            if pr.get("draft") and repo not in DRAFT_REPOS:
                drafts += 1
                continue
            # Gate auteur : métadonnées GitHub fiables (jamais le simple username).
            assoc = pr.get("author_association") or ""
            if AUTHOR_ASSOCIATIONS and assoc not in AUTHOR_ASSOCIATIONS:
                authors += 1
                log("author skipped", repo=repo, pr=number, association=assoc)
                continue
            head_sha = head.get("sha")
            base_sha = (pr.get("base") or {}).get("sha")
            title = (pr.get("title") or "")[:200]
            if not (head_sha and base_sha):
                continue
            is_new, _ = qdb.enqueue(con, repo, number, base_sha, head_sha, title)
            if is_new:
                created += 1
                log("enqueued", repo=repo, pr=number, head=head_sha[:12])
            else:
                dedup += 1
    return created, dedup, forks, drafts, authors, prefix, errors


def main_loop():
    log("poller démarré (advisory réel)", repos=REPOS, interval_s=INTERVAL,
        draft_repos=sorted(DRAFT_REPOS), prefixes=BRANCH_PREFIXES,
        author_associations=sorted(AUTHOR_ASSOCIATIONS))
    while True:
        con = qdb.connect(CFG["db_path"])
        try:
            c, d, f, dr, a, p, e = scan_once(con)
            log("scan", created=c, dedup=d, forks=f, drafts=dr, authors=a, prefix=p, errors=e)
            try:
                with open(METRICS_DIR / "scan.jsonl", "a") as fh:
                    fh.write(json.dumps({"ts": time.time(), "created": c, "dedup": d,
                                         "forks": f, "drafts": dr, "authors": a,
                                         "prefix": p, "errors": e}) + "\n")
            except Exception:
                pass
        except Exception as ex:
            log("scan error", err=str(ex)[-300:])
        finally:
            con.close()
        try:
            (STATE / "poller.heartbeat").write_text(str(time.time()))
        except Exception:
            pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main_loop()
