#!/usr/bin/env python3
"""feedback.py — qualification humaine des findings (mécanisme, ne modifie AUCUN
finding passé ni GitHub ; écrit seulement state/feedback.jsonl).

Labels : confirmed | false_positive | unclear | obsolete | duplicate | not_reviewed
  - obsolete : valide au SHA reviewé mais déjà corrigé depuis (valid=true historique).
  - duplicate : même cause racine qu'un autre finding (pas une anomalie indépendante).
Champs structurés (optionnels) : severity_human, confidence, evidence — la sévérité
Muse est reprise du verdict (severity_muse). Re-labelliser remplace la ligne.

Usage :
  python3 transport/feedback.py show <job_id>                          # findings + labels
  python3 transport/feedback.py label <job_id> <idx|all> <label> [note] [--sev major] [--conf high] [--ev "preuve courte"]
"""
import argparse, json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import envfile  # noqa: E402
envfile.load()
import observe  # noqa: E402

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent
LABELS = ("confirmed", "false_positive", "unclear", "obsolete", "duplicate", "not_reviewed")
SEVERITIES = ("critical", "major", "minor", "info")
CONFIDENCES = ("high", "medium", "low")


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
    existing = observe.effective_feedback(STATE).get(job_id, {})
    print(f"{job_id}  state={r['state']}  verdict={verdict.get('status')}  findings={len(verdict.get('findings') or [])}")
    for i, f in enumerate(verdict.get("findings") or []):
        e = existing.get(i, {})
        lab = e.get("label", "not_reviewed")
        extra = ""
        if e.get("severity_human"):
            extra += f" sev_human={e['severity_human']}"
        if e.get("confidence"):
            extra += f" conf={e['confidence']}"
        print(f"[{i}] {lab:13s} muse={f.get('severity','?'):8s}{extra} {f.get('file','')}:{f.get('line','')} — {f.get('title','')[:60]}")
    return 0


def label(job_id, idx, lab, note="", severity_human=None, confidence=None, evidence=""):
    """Enregistre/remplace la qualification d'un finding. Retour 0/1/2."""
    if lab not in LABELS:
        print(f"label invalide: {lab} (attendu: {', '.join(LABELS)})")
        return 2
    if severity_human is not None and severity_human not in SEVERITIES:
        print(f"severity invalide: {severity_human} (attendu: {', '.join(SEVERITIES)})")
        return 2
    if confidence is not None and confidence not in CONFIDENCES:
        print(f"confidence invalide: {confidence} (attendu: {', '.join(CONFIDENCES)})")
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
        f = findings[i]
        entries.append({"ts": time.time(), "job_id": job_id, "finding_idx": i,
                        "label": lab, "note": (note or "")[:500],
                        "title": (f.get("title") or "")[:160],
                        "severity_muse": (f.get("severity") or "info").lower(),
                        "severity_human": (severity_human or "").lower() or None,
                        "confidence": (confidence or "").lower() or None,
                        "evidence": (evidence or "")[:400]})
    p = observe.feedback_dir(STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    # upsert : re-labelliser (job, idx) remplace l'ancienne ligne (append-only log utile)
    keep = []
    if p.exists():
        jids = {e["job_id"] for e in entries}
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except Exception:
                continue
            if old.get("job_id") in jids and old.get("finding_idx") in {e["finding_idx"] for e in entries}:
                continue
            keep.append(old)
    with open(p, "w") as fh:
        for e in keep + entries:
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
    l.add_argument("--sev", dest="sev", choices=SEVERITIES)
    l.add_argument("--conf", dest="conf", choices=CONFIDENCES)
    l.add_argument("--ev", dest="ev", default="")
    args = ap.parse_args()
    if args.cmd == "show":
        return show(args.job_id)
    return label(args.job_id, args.idx, args.label, args.note or "",
                 severity_human=args.sev, confidence=args.conf, evidence=args.ev)


if __name__ == "__main__":
    sys.exit(main())
