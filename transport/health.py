#!/usr/bin/env python3
"""health.py — healthcheck du service pr-reviewer (sans jamais démarrer de session
Muse ni consommer de quota). Sortie JSON structurée, exit 0 = ok, 1 = degraded.

Vérifie : poller vivant (heartbeat), worker vivant (heartbeat), SQLite accessible,
profondeur de file, dernier scan GitHub, auth GitHub valide, conteneur fb-vps,
disque. Jamais de valeur de token dans la sortie.

Usage : python3 health.py [--json]
"""
import json, os, pathlib, shutil, subprocess, sys, time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import envfile
envfile.load()
import github_client as gh

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent
STALE_AFTER_S = float(CFG.get("health_stale_after_s", 120))


def check_heartbeat(name, max_age):
    path = STATE / f"{name}.heartbeat"
    try:
        age = time.time() - float(path.read_text().strip())
        return ("ok" if age <= max_age else "stale"), round(age, 1)
    except Exception:
        return "missing", None


def main():
    out = {"status": "ok", "ts": time.time(),
           "poller": None, "worker": None, "queue": {},
           "github": None, "freebuff_runtime": None, "disk_free_gb": None}
    # poller : heartbeat + âge du dernier scan
    st, age = check_heartbeat("poller", STALE_AFTER_S)
    poller = {"heartbeat": st, "age_s": age}
    scan_path = STATE / "metrics" / "scan.jsonl"
    last_scan = None
    try:
        if scan_path.exists():
            last_scan = json.loads(scan_path.read_text().strip().splitlines()[-1])
            last_scan["ts_age_s"] = round(time.time() - last_scan["ts"], 1)
    except Exception:
        pass
    poller["last_scan"] = last_scan
    out["poller"] = poller
    # worker : heartbeat
    st_w, age_w = check_heartbeat("worker", STALE_AFTER_S)
    out["worker"] = {"heartbeat": st_w, "age_s": age_w}
    # SQLite + queue
    try:
        import sqlite3
        con = sqlite3.connect(CFG["db_path"], timeout=5)
        counts = dict(con.execute("SELECT state, count(*) FROM jobs GROUP BY state").fetchall())
        con.close()
        out["queue"] = {k: counts.get(k, 0) for k in ("pending", "running", "retry", "done", "error")}
    except Exception as e:
        out["queue"] = {"error": str(e)[:120]}
        out["status"] = "degraded"
    # GitHub auth : chaque repo de l'allowlist doit répondre (1 GET léger par
    # repo, jamais la valeur du token). Multi-repo : un repo inaccessible → degraded.
    repos = CFG.get("repos") or []
    max_checks = int(CFG.get("health_max_repos", 25))
    checked = repos[:max_checks]
    failures = []
    first = None
    for r in checked:
        try:
            d = gh._req("GET", f"/repos/{r}")
            if first is None:
                first = d
        except RuntimeError as e:
            failures.append({"repo": r, "err": str(e)[-120:]})
    out["github"] = {"repo": checked[0] if checked else None,
                      "monitored": len(repos), "checked": len(checked),
                      "ok": not failures, "failures": failures,
                      "default_branch": (first or {}).get("default_branch"),
                      "private": (first or {}).get("private")}
    if failures:
        out["status"] = "degraded"
    # fb-vps (container du reviewer — jamais de session Muse démarrée ici)
    try:
        p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                            CFG["worker_container"]],
                           capture_output=True, text=True, timeout=15)
        out["freebuff_runtime"] = {"container": CFG["worker_container"],
                                   "running": p.stdout.strip() == "true",
                                   "err": (p.stderr or "")[-120:] if p.returncode != 0 else None}
        if p.stdout.strip() != "true":
            out["status"] = "degraded"
    except Exception as e:
        out["freebuff_runtime"] = {"container": CFG["worker_container"], "running": False,
                                   "err": str(e)[:120]}
        out["status"] = "degraded"
    # disque
    try:
        du = shutil.disk_usage("/")
        out["disk_free_gb"] = round(du.free / 1e9, 1)
        if du.free / du.total < 0.05:
            out["status"] = "degraded"
    except Exception:
        pass
    for k in ("poller", "worker"):
        hb = out[k] and out[k].get("heartbeat")
        if hb in ("missing", "stale"):
            out["status"] = "degraded"
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
