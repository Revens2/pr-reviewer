#!/usr/bin/env python3
"""report.py — rapport opérationnel advisory (lecture seule, aucun quota, aucune review).

Usage :
  python3 transport/report.py [--since 7d|30d|all] [--json]

Source : base SQLite des jobs + verdicts (même source que le healthcheck ; le
fichier metrics/*.jsonl reste une trace redondante). Ne modifie rien, ne lance
jamais de session Muse.
"""
import argparse, json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import envfile  # noqa: E402
envfile.load()
import observe  # noqa: E402

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent


def aggregate(jobs):
    a = {"total": len(jobs), "real": 0, "fixture": 0, "unknown": 0,
         "pass": 0, "block": 0, "error": 0, "certified": 0, "not_certified": 0,
         "errors_by_class": {}, "severity": {s: 0 for s in observe.SEVERITIES},
         "retries_total": 0, "latency_total": [], "latency_review": [],
         "by_kind": {"real": {"pass": 0, "block": 0, "error": 0},
                     "fixture": {"pass": 0, "block": 0, "error": 0},
                     "unknown": {"pass": 0, "block": 0, "error": 0}}}
    for j in jobs:
        a[j["kind"]] += 1
        if j["state"] == "done" and j["status"] == "PASS":
            a["pass"] += 1
            a["by_kind"][j["kind"]]["pass"] += 1
        elif j["state"] == "done" and j["status"] == "BLOCK":
            a["block"] += 1
            a["by_kind"][j["kind"]]["block"] += 1
        else:
            a["error"] += 1
            a["by_kind"][j["kind"]]["error"] += 1
            cls = j["error_class"] or (j["status"] or "UNKNOWN")
            a["errors_by_class"][cls] = a["errors_by_class"].get(cls, 0) + 1
        if j["certified"]:
            a["certified"] += 1
        else:
            a["not_certified"] += 1
        for s, n in j["severity"].items():
            a["severity"][s] += n
        a["retries_total"] += j["retries"]
        if j["state"] == "done":
            a["latency_total"].append(j["total_latency_s"])
            a["latency_review"].append(j["review_duration_s"])
    a["latency_p50"] = observe.percentile(a["latency_total"], 0.5)
    a["latency_p95"] = observe.percentile(a["latency_total"], 0.95)
    a["review_p50"] = observe.percentile(a["latency_review"], 0.5)
    a["review_p95"] = observe.percentile(a["latency_review"], 0.95)
    a["cert_rate"] = round(a["certified"] / a["total"], 3) if a["total"] else None
    return a


def main():
    ap = argparse.ArgumentParser(description="rapport advisory pr-reviewer (read-only)")
    ap.add_argument("--since", default="30d", help="all|7d|30d|Nd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    jobs = observe.load_jobs(CFG["db_path"], observe.since_ts(args.since))
    agg = aggregate(jobs)
    eff = observe.effective_feedback(STATE)
    fb = observe.feedback_stats(jobs, eff)
    agg["feedback"] = fb
    if args.json:
        print(json.dumps(agg, indent=2, ensure_ascii=False))
        return
    print(f"== Rapport advisory (depuis {args.since}) ==")
    print(f"reviews total: {agg['total']}  (real={agg['real']}, fixture={agg['fixture']}, legacy_unknown={agg['unknown']})")
    print(f"PASS={agg['pass']}  BLOCK={agg['block']}  ERROR={agg['error']}")
    print(f"certifiées Muse: {agg['certified']} ({agg['cert_rate']})  non certifiées: {agg['not_certified']}")
    print(f"latence totale p50/p95: {agg['latency_p50']}/{agg['latency_p95']} s ; review p50/p95: {agg['review_p50']}/{agg['review_p95']} s")
    print(f"retries total: {agg['retries_total']} ; findings par sévérité: {agg['severity']}")
    print(f"errors par classe: {agg['errors_by_class'] or 'aucun'}")
    c = fb["counts"]
    print(f"qualification humaine: {fb['findings_qualified']} finding(s) qualifié(s) — "
          f"confirmed={c['confirmed']} false_positive={c['false_positive']} unclear={c['unclear']} "
          f"obsolete={c['obsolete']} duplicate={c['duplicate']}")
    print(f"  confirmed_rate={fb['confirmed_rate']} false_positive_rate={fb['false_positive_rate']} "
          f"(dénominateur: {fb['denominator']})")
    print(f"  accord sévérité Muse/humain: {fb['severity_agreement']}")
    print("Freebucks consommés / reviews-par-session : non observables proprement — "
          "solde ponctuel dans state/balance.json (jamais de session démarrée pour mesurer).")


if __name__ == "__main__":
    main()
