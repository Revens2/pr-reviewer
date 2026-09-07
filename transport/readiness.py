#!/usr/bin/env python3
"""readiness.py — évalue objectivement si le passage en required check est
envisageable. NE MODIFIE RIEN sur GitHub, ne lance aucune review.

Usage :
  python3 transport/readiness.py [--json]

Statuts :
  NOT_ENOUGH_DATA  — pas assez de reviews réelles qualifiées pour conclure
  NOT_READY        — des critères qualitatifs/quantitatifs ne sont pas remplis
  CANDIDATE_READY  — données suffisantes et critères remplis (décision finale
                     humaine ; rien n'est activé ici)

Les seuils quantitatifs ci-dessous sont PROVISOIRES (floor minimal de signal) :
ils seront ajustés après observation réelle — ce script affiche d'abord les
données, il ne fabrique aucune statistique.
"""
import argparse, json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import envfile  # noqa: E402
envfile.load()
import observe  # noqa: E402

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
STATE = pathlib.Path(CFG["db_path"]).parent

# ---- seuils PROVISOIRES (à calibrer sur l'observation, jamais gravés dans GitHub)
MIN_REAL_REVIEWS = 5          # floor minimal de signal
MIN_CERT_RATE = 0.9           # % de reviews certifiées Muse sur les réelles
MAX_TECH_ERROR_RATE = 0.2     # % de jobs error/technique toléré sur les réelles
MIN_LABELED_RATE = 0.8        # % des findings BLOCK réels qualifiés par un humain
MAX_FALSE_POSITIVE_RATE = 0.25  # parmi les findings BLOCK réels qualifiés


def _status(jobs, labels):
    real = [j for j in jobs if j["kind"] == "real"]
    reasons = []
    n = len(real)
    # sous le floor de signal, le statut est NOT_ENOUGH_DATA (pas de conclusion possible)
    if n < MIN_REAL_REVIEWS:
        return "NOT_ENOUGH_DATA", [f"{n} review(s) réelle(s) < floor de signal {MIN_REAL_REVIEWS}"], real
    cert = sum(1 for j in real if j["certified"])
    cert_rate = cert / n
    if cert_rate < MIN_CERT_RATE:
        reasons.append(f"certification {cert_rate:.0%} < {MIN_CERT_RATE:.0%}")
    err = sum(1 for j in real if j["state"] != "done")
    err_rate = err / n
    if err_rate > MAX_TECH_ERROR_RATE:
        reasons.append(f"erreurs techniques {err_rate:.0%} > {MAX_TECH_ERROR_RATE:.0%}")
    block_findings = [j for j in real if j["status"] == "BLOCK"]
    total_bf = sum(len(j["findings"]) for j in block_findings)
    # Dénominateurs (documentés) : `unclear` n'est ni une décision ni un FP —
    # il est exclu de l'éligibilité (couverture) et des décisions binaires ;
    # obsolete/duplicate = qualifiés non-FP (pas des anomalies indépendantes).
    qualified = unclear = fp = confirmed = 0
    for j in block_findings:
        lj = labels.get(j["id"], {})
        for i in range(len(j["findings"])):
            lab = lj.get(i)
            if lab is None or lab == "not_reviewed":
                continue
            if lab == "unclear":
                unclear += 1
            elif lab in ("confirmed", "false_positive", "obsolete", "duplicate"):
                qualified += 1
                if lab == "false_positive":
                    fp += 1
                if lab == "confirmed":
                    confirmed += 1
    decision_eligible = total_bf - unclear
    if decision_eligible:
        coverage = qualified / decision_eligible
        if coverage < MIN_LABELED_RATE:
            reasons.append(f"findings BLOCK qualifiés {coverage:.0%} < {MIN_LABELED_RATE:.0%} (hors unclear)")
        decided = confirmed + fp
        if decided:
            fp_rate = fp / decided
            if fp_rate > MAX_FALSE_POSITIVE_RATE:
                reasons.append(f"faux positifs déclarés {fp_rate:.0%} > {MAX_FALSE_POSITIVE_RATE:.0%}")
    # critères qualitatifs affichés (évaluation humaine, pas automatisable ici)
    qualitative = [
        "0 corruption / faux BLOCK grave non expliqué — à confirmer par revue humaine",
        "recovery restart prouvé (réconciliation running orphelin testée)",
        "consommation Freebucks comprise — solde non lisible proprement (voir report)",
        "stabilité service observée plusieurs jours — à confirmer",
    ]
    return ("CANDIDATE_READY" if not reasons else "NOT_READY"), reasons, real


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    jobs = observe.load_jobs(CFG["db_path"], 0.0)
    labels = observe.labels_by_job(STATE)
    status, reasons, real = _status(jobs, labels)
    eff = observe.effective_feedback(STATE)
    fb = observe.feedback_stats(jobs, eff)
    real_blocks = [j for j in real if j["status"] == "BLOCK"]
    block_qual = [observe.block_quality(j, eff) for j in real_blocks]
    # reviews qualifiées = BLOCK réels avec ≥1 finding qualifié (décision humaine)
    human_qualified = [j for j in real_blocks
                       if any(e.get("label") not in (None, "not_reviewed")
                              for e in eff.get(j["id"], {}).values())]
    info = {
        "status": status,
        "real_reviews": len(real),
        "real_block_reviews": len(real_blocks),
        "human_qualified_reviews": len(human_qualified),
        "block_quality": block_qual,
        "feedback": fb,
        "reasons": reasons,
        "total_jobs": len(jobs),
        "thresholds": {k: v for k, v in globals().items()
                       if k.startswith(("MIN_", "MAX_")) and isinstance(v, (int, float))},
    }
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(f"required-readiness: {status}")
        print(f"  reviews réelles: {len(real)} / total jobs: {len(jobs)}")
        print(f"  BLOCK réels: {len(real_blocks)} ; qualifiés humainement: {len(human_qualified)} ; "
              f"block_quality: {block_qual or 'aucun BLOCK réel'}")
        c = fb["counts"]
        print(f"  qualification: confirmed={c['confirmed']} false_positive={c['false_positive']} "
              f"unclear={c['unclear']} obsolete={c['obsolete']} duplicate={c['duplicate']} "
              f"| confirmed_rate={fb['confirmed_rate']} fp_rate={fb['false_positive_rate']}")
        for r in reasons:
            print(f"  - {r}")
        if status == "CANDIDATE_READY":
            print("  Données suffisantes — décision finale humaine requise. Rien n'est activé.")
    # exit 0 même pour NOT_READY : le statut est une information, pas une erreur


if __name__ == "__main__":
    main()
