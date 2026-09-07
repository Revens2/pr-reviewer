#!/usr/bin/env python3
"""assemble_verdict.py — assemble le verdict certifié d'une review.

Entrées (job dir) :
  - result.json          : verdict du reviewer (écrit par Freebuff dans /output)
  - transcript-<job>.txt : capture TUI (fallback sentinelles)
  - state-<job>.json     : état driver (AUTH_REQUIRED / TIMEOUT...)
  - evidence-<job>/      : preuves modèle (model_probe.sh)

Sortie : verdict.json conforme au contrat, avec champs de certification.
Politique : jamais de PASS si le modèle n'est pas certifié (champ explicite).
"""
import argparse, json, re, sys, pathlib

SENTINEL_RE = re.compile(
    r"<<<PR_REVIEW_RESULT_V1>>>(.*?)<<<END_PR_REVIEW_RESULT_V1>>>", re.S)
SEV_BLOCK = {"critical", "major"}
SEVERITIES = {"critical", "major", "minor", "info"}

def load_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return None

def parse_result(job_dir: pathlib.Path, job: str):
    rj = job_dir / "result.json"
    if rj.exists():
        data = load_json(rj)
        if data and data.get("schema_version") == 1:
            return data, "file"
    # fallback transcript
    for tr in job_dir.glob(f"transcript-{job}.txt"):
        txt = tr.read_text(errors="replace")
        m = SENTINEL_RE.search(txt)
        if m:
            try:
                data = json.loads(m.group(1))
                return data, "sentinel"
            except Exception:
                pass
    # fallback : transcript générique (si nom différent)
    for tr in job_dir.glob("transcript-*.txt"):
        txt = tr.read_text(errors="replace")
        m = SENTINEL_RE.search(txt)
        if m:
            try:
                return json.loads(m.group(1)), "sentinel-glob"
            except Exception:
                pass
    return None, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--driver-rc", type=int, default=0)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--model-requested", default="meta/muse-spark-1.3-contributor")
    args = ap.parse_args()

    jd = pathlib.Path(args.job_dir)
    job = f"{args.repo}/{args.pr}/{args.head_sha}".replace("/", "_").replace(":", "_")

    base = {
        "schema_version": 1,
        "status": "REVIEW_UNAVAILABLE",
        "model_requested": args.model_requested,
        "model_verified": None,
        "model_certified": False,
        "head_sha": args.head_sha,
        "summary": "",
        "findings": [],
        "error_class": None,
        "result_source": None,
    }

    state = load_json(jd / f"state-{job}.json")
    rc = args.driver_rc
    requested_sha = args.head_sha

    # Échecs driver durs → fail-closed, jamais PASS (pas de parsing possible).
    if rc in (10, 20, 30):
        cls = {10: "AUTH_REQUIRED", 20: "TIMEOUT", 30: "INSTANCE_BUSY"}[rc]
        base["error_class"] = cls
        base["summary"] = f"Review driver failed (rc={rc})."
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 1

    # rc=3 : sentinelle seulement (le driver a vu la fin mais pas le fichier).
    data, src = parse_result(jd, job)
    if not data and rc == 3:
        data, src = parse_result(jd, job)  # idempotent, parse transcripts
    if not data:
        base["error_class"] = "PARSER_ERROR" if rc == 0 else f"DRIVER_ERROR_{rc}"
        base["summary"] = "No parseable result found."
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 2

    # Normalisation
    findings = data.get("findings", [])
    clean = []
    for f in findings:
        sev = f.get("severity", "info")
        if sev not in SEVERITIES:
            sev = "info"
        clean.append({
            "severity": sev,
            "file": f.get("file"),
            "line": f.get("line"),
            "title": f.get("title", ""),
            "reason": f.get("reason", ""),
            "suggested_fix": f.get("suggested_fix"),
        })
    status = data.get("status", "REVIEW_UNAVAILABLE")
    if status not in ("PASS", "BLOCK", "REVIEW_UNAVAILABLE"):
        status = "REVIEW_UNAVAILABLE"
    if status == "PASS" and any(f["severity"] in SEV_BLOCK for f in clean):
        status = "BLOCK"   # surclassement de sécurité : jamais PASS avec bloquant

    base.update({
        "status": status,
        "head_sha": requested_sha,
        "summary": data.get("summary", ""),
        "findings": clean,
        "result_source": src,
    })

    # Certification modèle (hors bande). Aucune preuve → non certifié.
    ev = jd / "evidence" / "model.json"
    if not ev.exists():
        for d in jd.glob("evidence-*/model.json"):
            ev = d
            break
    if ev.exists():
        me = load_json(ev) or {}
        base["model_verified"] = me.get("model_observed")
        base["model_certified"] = bool(me.get("certified"))
        base["model_evidence"] = me.get("evidence", [])
        if not me.get("certified"):
            base["error_class"] = base.get("error_class") or me.get("state", "MODEL_UNVERIFIED")
    else:
        base["model_verified"] = None
        base["model_certified"] = False
        base["error_class"] = "MODEL_UNVERIFIED"

    # Cohérence head SHA (stale) : si le reviewer a retourné un autre SHA que celui demandé
    # → review invalide pour le SHA courant (une review d'un ancien SHA ne valide jamais le nouveau).
    if data.get("head_sha") and data["head_sha"] != requested_sha:
        base["status"] = "REVIEW_UNAVAILABLE"
        base["error_class"] = "STALE_SHA_MISMATCH"

    out = jd / "verdict.json"
    out.write_text(json.dumps(base, ensure_ascii=False, indent=2))
    print(json.dumps(base, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
