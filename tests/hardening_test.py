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
        self.fb.STATE = pathlib.Path(TMP)
        self.state = pathlib.Path(TMP)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
