#!/usr/bin/env python3
"""observe.py — helpers de lecture (jamais d'écriture GitHub, jamais de review,
jamais de quota) : charge les jobs SQLite + verdicts pour report/readiness/feedback.
Aucun secret manipulé (verdicts = preuves non-secrètes, token jamais lu)."""
import json, pathlib, sqlite3, time

SEVERITIES = ("critical", "major", "minor", "info")


def connect(db_path):
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def parse_verdict(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def severity_counts(verdict):
    counts = {s: 0 for s in SEVERITIES}
    for f in verdict.get("findings") or []:
        sev = (f.get("severity") or "info").lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def job_kind(head_ref, repo=None):
    """real = branche interne hors test (advisory réel) ; fixture = test/* ;
    unknown = lignes legacy sans head_ref (base créée avant observabilité)."""
    hr = (head_ref or "").strip()
    if not hr:
        return "unknown"
    return "fixture" if hr.lower().startswith(("test/", "fixture/", "tmp/")) else "real"


def load_jobs(db_path, since_ts=0.0):
    con = connect(db_path)
    rows = con.execute(
        "SELECT * FROM jobs WHERE finished_at IS NOT NULL AND finished_at>=? ORDER BY finished_at",
        (since_ts,)).fetchall()
    con.close()
    out = []
    for r in rows:
        v = parse_verdict(r["verdict"])
        created = r["created_at"] or 0
        started = r["started_at"] or created
        finished = r["finished_at"] or created
        sev = severity_counts(v)
        status = v.get("status")
        certified = bool(v.get("model_certified"))
        out.append({
            "id": r["id"], "repo": r["repo"], "pr": r["pr"],
            "head_sha": r["head_sha"], "head_ref": r["head_ref"] or "",
            "author_association": r["author_association"] or "",
            "kind": job_kind(r["head_ref"]),
            "state": r["state"], "error_class": r["error_class"],
            "retries": r["retries"] or 0,
            "queue_delay_s": max(0.0, started - created),
            "review_duration_s": max(0.0, finished - started),
            "total_latency_s": max(0.0, finished - created),
            "finished_at": finished,
            "status": status,
            "certified": certified,
            "model_verified": v.get("model_verified"),
            "model_requested": v.get("model_requested"),
            "severity": sev,
            "findings_count": sum(sev.values()),
            "findings": v.get("findings") or [],
        })
    return out


def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def since_ts(spec):
    """--since all|7d|30d → epoch. Défaut 30d."""
    if spec in (None, "", "all"):
        return 0.0
    n = {"1d": 1, "7d": 7, "14d": 14, "30d": 30}.get(spec)
    if not n and spec.endswith("d"):
        try:
            n = int(spec[:-1])
        except ValueError:
            n = 7
    return time.time() - (n or 7) * 86400


FEEDBACK_PATH = "feedback.jsonl"


def feedback_dir(state_dir):
    return pathlib.Path(state_dir) / FEEDBACK_PATH


def load_feedback(state_dir):
    p = feedback_dir(state_dir)
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def labels_by_job(state_dir):
    """job_id -> {finding_idx: label}."""
    by = {}
    for e in load_feedback(state_dir):
        by.setdefault(e["job_id"], {})[e["finding_idx"]] = e["label"]
    return by
