#!/usr/bin/env python3
"""hardening_test.py — tests offline des ajouts durcissement/observabilité :
docker_guard (wrapper strict), observe/report (agrégats), readiness (statuts),
feedback (écriture/lecture état local). Aucun réseau, aucun quota modèle."""
import json, os, pathlib, sqlite3, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="prh-")
os.environ["TRANSPORT_CONFIG"] = json.dumps({
    "db_path": str(pathlib.Path(TMP) / "jobs.db"),
    "jobs_root": str(pathlib.Path(TMP) / "jobs"),
    "repos": ["Revens2/agent-island"],
})
sys.path.insert(0, str(HERE / "transport"))
import envfile  # noqa: E402
envfile.load()
import docker_guard  # noqa: E402
import db as qdb  # noqa: E402
import observe  # noqa: E402


def finished_job(repo, pr, head, head_ref="", assoc="OWNER", status="PASS",
                 certified=True, error_class=None, findings=None,
                 state="done", n_retries=0):
    con = qdb.connect(os.environ["TRANSPORT_CONFIG"] and json.loads(os.environ["TRANSPORT_CONFIG"])["db_path"])
    created = time.time() - 200
    started = time.time() - 150
    finished = time.time() - 20
    qdb.enqueue(con, repo, pr, "b" * 40, head, head_ref=head_ref,
                author_association=assoc)
    verdict = {"schema_version": 1, "status": status, "head_sha": head,
               "summary": "s", "findings": findings or [],
               "model_certified": certified,
               "model_verified": "meta/muse-spark-1.3-contributor",
               "model_requested": "meta/muse-spark-1.3-contributor"}
    if error_class:
        verdict["error_class"] = error_class
    con.execute("UPDATE jobs SET state=?, verdict=?, created_at=?, started_at=?,"
                " finished_at=?, retries=?, error_class=? WHERE pr=? AND head_sha=?",
                (state, json.dumps(verdict), created, started, finished, n_retries,
                 error_class, pr, head))
    con.commit()
    con.close()


class TestDockerGuard(unittest.TestCase):
    def test_exec_fixed(self):
        self.assertEqual(
            docker_guard.docker_argv("exec", ["bash", "-lc", "echo hi"]),
            ["docker", "exec", "-i", "-u", "reviewer", "fb-vps", "bash", "-lc", "echo hi"])

    def test_start_and_inspect(self):
        self.assertEqual(docker_guard.docker_argv("start", []),
                         ["docker", "start", "fb-vps"])
        self.assertEqual(docker_guard.docker_argv("inspect-running", []),
                         ["docker", "inspect", "-f", "{{.State.Running}}", "fb-vps"])

    def test_rejections(self):
        with self.assertRaises(docker_guard.GuardError):
            docker_guard.docker_argv("exec", [])                 # pas de commande
        with self.assertRaises(docker_guard.GuardError):
            docker_guard.docker_argv("exec", ["--privileged", "id"])  # option en tête
        with self.assertRaises(docker_guard.GuardError):
            docker_guard.docker_argv("exec", ["bash", "a\nb"])   # séparateur
        with self.assertRaises(docker_guard.GuardError):
            docker_guard.docker_argv("rm", ["fb-vps"])           # verbe inconnu
        with self.assertRaises(docker_guard.GuardError):
            docker_guard.docker_argv("start", ["fb-vps", "extra"])
        # une commande multi-tokens reste VALIDE (argv direct, pas de shell docker)
        self.assertEqual(
            docker_guard.docker_argv("exec", ["bash", "-c", "ls / && true"]),
            ["docker", "exec", "-i", "-u", "reviewer", "fb-vps", "bash", "-c", "ls / && true"])

    def test_container_identity_is_literal(self):
        # l'appelant ne peut PAS changer conteneur/user (position fixes)
        cmd = docker_guard.docker_argv("exec", ["cat", "/etc/hostname"])
        self.assertEqual(cmd[5], "fb-vps")


class TestObserve(unittest.TestCase):
    def setUp(self):
        self.dbp = pathlib.Path(TMP) / f"obs{time.time_ns()}.db"
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(self.dbp)})
        qdb.connect(self.dbp).close()  # schéma créé
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(self.dbp)})

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(pathlib.Path(TMP) / "jobs.db")})

    def test_job_kind(self):
        self.assertEqual(observe.job_kind("feat/x"), "real")
        self.assertEqual(observe.job_kind("test/pr-reviewer-b"), "fixture")
        self.assertEqual(observe.job_kind(""), "unknown")

    def test_aggregate_counts(self):
        finished_job("R/a", 1, "h" * 40, head_ref="feat/x", status="BLOCK",
                     findings=[{"severity": "major"}, {"severity": "minor"}])
        finished_job("R/a", 2, "g" * 40, head_ref="feat/y", status="PASS", findings=[])
        finished_job("R/a", 3, "f" * 40, head_ref="feat/z", status="PASS",
                     certified=False, error_class="MODEL_UNVERIFIED", state="error")
        finished_job("R/a", 4, "e" * 40, head_ref="test/fix", status="PASS")
        jobs = observe.load_jobs(self.dbp, 0.0)
        import report as rp
        agg = rp.aggregate(jobs)
        self.assertEqual(agg["total"], 4)
        self.assertEqual(agg["real"], 3)
        self.assertEqual(agg["fixture"], 1)
        self.assertEqual(agg["pass"], 2)
        self.assertEqual(agg["block"], 1)
        self.assertEqual(agg["error"], 1)
        self.assertEqual(agg["severity"]["major"], 1)
        self.assertEqual(agg["severity"]["minor"], 1)
        self.assertIsNotNone(agg["latency_p50"])

    def test_aggregate_by_repo(self):
        # dimension repository : les jobs restent agrégés sous leur propre repo
        finished_job("Revens2/repo-a", 1, "h" * 40, head_ref="feat/x", status="BLOCK",
                     findings=[{"severity": "major"}])
        finished_job("Revens2/repo-a", 2, "g" * 40, head_ref="feat/y", status="PASS")
        finished_job("Revens2/repo-b", 1, "f" * 40, head_ref="feat/z", status="PASS")
        finished_job("Revens2/repo-b", 2, "e" * 40, head_ref="test/fix", status="PASS")
        jobs = observe.load_jobs(self.dbp, 0.0)
        import report as rp
        agg = rp.aggregate(jobs)
        a = agg["by_repo"]["Revens2/repo-a"]
        b = agg["by_repo"]["Revens2/repo-b"]
        self.assertEqual((a["total"], a["pass"], a["block"]), (2, 1, 1))
        self.assertEqual((b["total"], b["pass"], b["block"]), (2, 2, 0))
        self.assertEqual((a["fixture"], b["fixture"]), (0, 1))
        self.assertEqual(agg["by_repo"].keys(), {"Revens2/repo-a", "Revens2/repo-b"})


class TestReadiness(unittest.TestCase):
    def setUp(self):
        self.dbp = pathlib.Path(TMP) / f"rd{time.time_ns()}.db"
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(self.dbp)})
        qdb.connect(self.dbp).close()  # schéma créé (base vide → NOT_ENOUGH_DATA)

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(pathlib.Path(TMP) / "jobs.db")})

    def test_not_enough_data(self):
        import readiness as rd
        jobs = observe.load_jobs(self.dbp, 0.0)
        st, reasons, real = rd._status(jobs, {})
        self.assertEqual(st, "NOT_ENOUGH_DATA")

    def test_one_real_is_still_not_enough(self):
        import readiness as rd
        finished_job("R/a", 1, "h" * 40, head_ref="feat/x", status="PASS")
        jobs = observe.load_jobs(self.dbp, 0.0)
        st, reasons, _ = rd._status(jobs, {})
        self.assertEqual(st, "NOT_ENOUGH_DATA")


class TestFeedback(unittest.TestCase):
    def setUp(self):
        self.dbp = pathlib.Path(TMP) / f"fb{time.time_ns()}.db"
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(self.dbp)})
        import feedback as fb
        self.fb = fb
        self.fb.CFG["db_path"] = str(self.dbp)   # job lu depuis cette base
        # state dir dédié PAR TEST (hermétique — pas de contamination entre tests)
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="fbs-"))
        self.fb.STATE = self.state

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(pathlib.Path(TMP) / "jobs.db")})

    def test_label_and_read(self):
        jid = f"R/a|7|{'c'*40}"
        # helper écrit dans la base pointée par TRANSPORT_CONFIG (self.dbp ici)
        finished_job("R/a", 7, "c" * 40, head_ref="feat/z", status="BLOCK",
                     findings=[{"severity": "major", "title": "bug"},
                               {"severity": "minor", "title": "style"}])
        self.assertEqual(self.fb.label(jid, "all", "confirmed"), 0)
        self.assertEqual(self.fb.label(jid, 1, "false_positive"), 0)
        labels = observe.labels_by_job(self.state)
        self.assertEqual(labels[jid][0], "confirmed")
        self.assertEqual(labels[jid][1], "false_positive")


class TestFeedbackQualify(unittest.TestCase):
    """Qualification étendue : labels obsolete/duplicate, sévérité humaine,
    upsert au re-label, dénominateurs (unclear exclu) et BLOCK quality."""

    def setUp(self):
        self.dbp = pathlib.Path(TMP) / f"fq{time.time_ns()}.db"
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(self.dbp)})
        import feedback as fb
        self.fb = fb
        self.fb.CFG["db_path"] = str(self.dbp)
        # state dir dédié PAR TEST (hermétique — pas de contamination entre tests)
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="fqs-"))
        self.fb.STATE = self.state
        self.jid = f"R/a|9|{'d'*40}"
        self.findings = [{"severity": "major", "title": "t1"},
                         {"severity": "minor", "title": "t2"},
                         {"severity": "major", "title": "t3"}]
        finished_job("R/a", 9, "d" * 40, head_ref="feat/q", status="BLOCK",
                     findings=self.findings)

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = json.dumps({"db_path": str(pathlib.Path(TMP) / "jobs.db")})

    def _job(self):
        jobs = observe.load_jobs(self.dbp, 0.0)
        return [j for j in jobs if j["id"] == self.jid][0]

    def test_extended_labels_and_upsert(self):
        self.assertEqual(self.fb.label(self.jid, 0, "obsolete"), 0)
        self.assertEqual(self.fb.label(self.jid, 1, "duplicate"), 0)
        self.assertEqual(self.fb.label(self.jid, 2, "unclear",
                                       severity_human="major", confidence="low"), 0)
        eff = observe.effective_feedback(self.state)
        self.assertEqual(eff[self.jid][0]["label"], "obsolete")
        self.assertEqual(eff[self.jid][1]["label"], "duplicate")
        self.assertEqual(eff[self.jid][2]["severity_human"], "major")
        # re-label remplace (upsert) — pas de doublon dans le fichier
        self.assertEqual(self.fb.label(self.jid, 0, "confirmed",
                                       severity_human="minor", confidence="high"), 0)
        eff2 = observe.effective_feedback(self.state)
        self.assertEqual(eff2[self.jid][0]["label"], "confirmed")
        self.assertEqual(eff2[self.jid][0]["severity_human"], "minor")
        lines = [l for l in (self.state / "feedback.jsonl").read_text().splitlines() if l.strip()]
        self.assertEqual(len([l for l in lines if f'"finding_idx": 0' in l]), 1)

    def test_invalid_label_and_severity_rejected(self):
        self.assertEqual(self.fb.label(self.jid, 0, "maybe"), 2)
        self.assertEqual(self.fb.label(self.jid, 0, "confirmed", severity_human="bloquant"), 2)
        self.assertEqual(self.fb.label(self.jid, 0, "confirmed", confidence="sure"), 2)

    def test_feedback_stats_denominator_excludes_unclear(self):
        self.fb.label(self.jid, 0, "confirmed", severity_human="major", confidence="high")
        self.fb.label(self.jid, 1, "false_positive", severity_human="info")
        self.fb.label(self.jid, 2, "unclear")
        job = self._job()
        fb = observe.feedback_stats([job], observe.effective_feedback(self.state))
        self.assertEqual(fb["counts"]["confirmed"], 1)
        self.assertEqual(fb["counts"]["false_positive"], 1)
        self.assertEqual(fb["counts"]["unclear"], 1)
        # décisions binaires = confirmed + fp → taux 0.5/0.5 (unclear exclu)
        self.assertEqual(fb["confirmed_rate"], 0.5)
        self.assertEqual(fb["false_positive_rate"], 0.5)
        self.assertEqual(fb["severity_agreement"]["exact"], 1)  # major==major
        self.assertEqual(fb["severity_agreement"]["muse_higher"], 1)  # fp: muse minor→info

    def test_block_quality(self):
        job = self._job()
        # non qualifié → UNKNOWN
        self.assertEqual(observe.block_quality(job, {}), "UNKNOWN")
        # 1 confirmed bloquant (major) → BLOCK_CORRECT
        self.fb.label(self.jid, 0, "confirmed", severity_human="major")
        self.fb.label(self.jid, 1, "false_positive")
        eff = observe.effective_feedback(self.state)
        self.assertEqual(observe.block_quality(job, eff), "BLOCK_CORRECT")
        # tout faux → BLOCK_INCORRECT
        self.fb.label(self.jid, 0, "false_positive")
        self.fb.label(self.jid, 2, "false_positive")
        eff = observe.effective_feedback(self.state)
        self.assertEqual(observe.block_quality(job, eff), "BLOCK_INCORRECT")
        # confirmés mineurs seulement → BLOCK_OVERREACH
        self.fb.label(self.jid, 0, "confirmed", severity_human="minor")
        self.fb.label(self.jid, 2, "confirmed", severity_human="minor")
        eff = observe.effective_feedback(self.state)
        self.assertEqual(observe.block_quality(job, eff), "BLOCK_OVERREACH")
        # obsolete seul = valide au SHA reviewé (sévérité Muse major en repli) → CORRECT
        self.fb.label(self.jid, 0, "obsolete")
        self.fb.label(self.jid, 1, "duplicate")
        self.fb.label(self.jid, 2, "obsolete")
        eff = observe.effective_feedback(self.state)
        self.assertEqual(observe.block_quality(job, eff), "BLOCK_CORRECT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
