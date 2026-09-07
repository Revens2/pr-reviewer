#!/usr/bin/env python3
"""feedback.py — qualification humaine des findings (mécanisme, ne modifie AUCUN
finding passé ni GitHub ; écrit seulement state/feedback.jsonl).

Usage :
  python3 transport/feedback.py show <job_id>                 # findings + labels
  python3 transport/feedback.py label <job_id> <idx|all> <confirmed|false_positive|unclear|not_reviewed> [note]
"""
import argparse, json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import envfile  # noqa: E402
envfile.load()
import observe  # noqa: E402

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent
LABELS = ("confirmed", "false_positive", "unclear", "not_reviewed")


def _job_row(db_path, job_id):
    con = observe.connect(db_path)
    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    return r


def show(job_id):
    r = _job_row(CFG["db_path"], job_id)
    if not r:
        print(f"job inconnu: {job_id}")
        return 1
    verdict = observe.parse_verdict(r["verdict"])
    existing = observe.labels_by_job(STATE).get(job_id, {})
    print(f"{job_id}  state={r['state']}  verdict={verdict.get('status')}  findings={len(verdict.get('findings') or [])}")
    for i, f in enumerate(verdict.get("findings") or []):
        lab = existing.get(i, "not_reviewed")
        print(f"[{i}] {lab:13s} {f.get('severity','?'):8s} {f.get('file','')}:{f.get('line','')} — {f.get('title','')[:70]}")
    return 0


def label(job_id, idx, lab, note=""):
    if lab not in LABELS:
        print(f"label invalide: {lab} (attendu: {', '.join(LABELS)})")
        return 2
    r = _job_row(CFG["db_path"], job_id)
    if not r:
        print(f"job inconnu: {job_id}")
        return 1
    verdict = observe.parse_verdict(r["verdict"])
    findings = verdict.get("findings") or []
    idxs = range(len(findings)) if idx == "all" else [int(idx)]
    entries = []
    for i in idxs:
        if i < 0 or i >= len(findings):
            print(f"index hors bornes: {i} (0..{len(findings)-1})")
            return 2
        entries.append({"ts": time.time(), "job_id": job_id, "finding_idx": i,
                        "label": lab, "note": (note or "")[:500],
                        "title": (findings[i].get("title") or "")[:160]})
    p = observe.feedback_dir(STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    print(f"labeled {len(entries)} finding(s) {lab} ({job_id})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="feedback humain sur les findings pr-reviewer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("job_id")
    l = sub.add_parser("label")
    l.add_argument("job_id"); l.add_argument("idx"); l.add_argument("label")
    l.add_argument("note", nargs="?")
    args = ap.parse_args()
    if args.cmd == "show":
        return show(args.job_id)
    return label(args.job_id, args.idx, args.label, args.note or "")


if __name__ == "__main__":
    sys.exit(main())
