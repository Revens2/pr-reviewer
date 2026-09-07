#!/usr/bin/env python3
"""transport_test.py — tests offline du transport (aucun quota modèle, aucun VPS).

Couvert :
  1. signature HMAC : ok / absente / invalide
  2. dédup repo+PR+SHA (même payload 2x → 1 job)
  3. events/actions non supportés ignorés
  4. draft ignoré sauf repo de test autorisé
  5. repo non autorisé ignoré
  6. queue : claim FIFO, retry borné (max_retries), état error après épuisement
  7. mapping statut→conclusion (PASS→success, BLOCK→failure, non certifié→neutral)
"""
import hashlib, hmac, json, os, pathlib, sqlite3, sys, tempfile, threading, time, unittest
from http.client import HTTPConnection

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "transport"))
TMP = tempfile.mkdtemp(prefix="prt-")
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["TRANSPORT_CONFIG"] = json.dumps({
    "listen_host": "127.0.0.1", "listen_port": 8799,
    "db_path": str(pathlib.Path(TMP) / "jobs.db"),
    "jobs_root": str(pathlib.Path(TMP) / "jobs"),
    "input_bind_root": str(pathlib.Path(TMP) / "input"),
    "worker_container": "fb-vps",
    "events": ["opened", "reopened", "ready_for_review", "synchronize"],
    "draft_repos": ["Revens2/agent-island"],
    "repos": ["Revens2/agent-island"],
    "max_retries": 2,
    "check_name": "Muse Semantic Review",
})
import db as qdb
import receiver as rcv
import worker as wk  # noqa: F401 (import pour verdict mapping)
import envfile

# Config initiale (chemins sous TMP, inscriptibles) — restaurée en tearDown pour
# que les imports ultérieurs (poller/worker) ne lisent JAMAIS config.example.json
# (chemins /home/... absents sur les runners CI).
INITIAL_TRANSPORT_CONFIG = os.environ["TRANSPORT_CONFIG"]


def sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def payload(action="opened", repo="Revens2/agent-island", pr=99, head="a" * 40, base="b" * 40, draft=False):
    return {"action": action,
            "repository": {"full_name": repo},
            "pull_request": {"number": pr, "draft": draft, "title": "T",
                             "head": {"sha": head}, "base": {"sha": base}}}


def post(port, body, headers_extra=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Content-Type": "application/json"}
    if headers_extra:
        hdrs.update(headers_extra)
    conn.request("POST", "/webhook", body=body, headers=hdrs)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


class TestSignature(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = rcv.ThreadingHTTPServer(("127.0.0.1", 0), rcv.Handler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_no_signature(self):
        st, _ = post(self.port, json.dumps(payload()).encode())
        self.assertEqual(st, 401)

    def test_bad_signature(self):
        st, _ = post(self.port, json.dumps(payload()).encode(),
                     {"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=deadbeef"})
        self.assertEqual(st, 401)

    def test_good_signature_and_dedup(self):
        body = json.dumps(payload()).encode()
        hdrs = {"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig(body)}
        st, data = post(self.port, body, hdrs)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(data)["created"])
        st2, data2 = post(self.port, body, hdrs)
        self.assertEqual(json.loads(data2)["created"], False)

    def test_wrong_event(self):
        body = json.dumps(payload()).encode()
        st, data = post(self.port, body, {"X-GitHub-Event": "issues",
                                          "X-Hub-Signature-256": sig(body)})
        self.assertEqual(st, 400)

    def test_draft_ignored_for_unknown_repo(self):
        body = json.dumps(payload(draft=True, repo="Revens2/other")).encode()
        st, data = post(self.port, body, {"X-GitHub-Event": "pull_request",
                                          "X-Hub-Signature-256": sig(body)})
        self.assertEqual(json.loads(data)["reason"], "draft ignored")

    def test_repo_unauthorized(self):
        body = json.dumps(payload(repo="Revens2/secret")).encode()
        st, data = post(self.port, body, {"X-GitHub-Event": "pull_request",
                                          "X-Hub-Signature-256": sig(body)})
        self.assertEqual(json.loads(data)["reason"], "repo not authorized")


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.dbp = pathlib.Path(TMP) / f"q{time.time_ns()}.db"
        self.con = qdb.connect(self.dbp)

    def tearDown(self):
        self.con.close()

    def test_fifo_and_states(self):
        c1 = qdb.enqueue(self.con, "R/a", 1, "b", "h1")
        c2 = qdb.enqueue(self.con, "R/a", 1, "b", "h2")
        self.assertTrue(c1[0] and c2[0])
        j1 = qdb.claim_next(self.con)
        self.assertEqual(j1["head_sha"], "h1")
        j2 = qdb.claim_next(self.con)
        self.assertEqual(j2["head_sha"], "h2")
        self.assertIsNone(qdb.claim_next(self.con))  # plus rien (running non réclamable)

    def test_retry_bounded(self):
        qdb.enqueue(self.con, "R/a", 2, "b", "hx")
        j = qdb.claim_next(self.con)
        # 1er échec → retry ok
        self.assertTrue(qdb.schedule_retry(self.con, j["id"], "INSTANCE_BUSY", "busy", max_retries=2, base_delay=0))
        j2 = qdb.claim_next(self.con)  # next_run=now → immédiat
        self.assertEqual(j2["state"], "running")
        # 2e échec → retry ok (2 retries autorisés)
        self.assertTrue(qdb.schedule_retry(self.con, j2["id"], "INSTANCE_BUSY", "busy", max_retries=2, base_delay=0))
        j3 = qdb.claim_next(self.con)
        # 3e échec → retries épuisés → error (pas de boucle infinie)
        self.assertFalse(qdb.schedule_retry(self.con, j3["id"], "INSTANCE_BUSY", "busy", max_retries=2, base_delay=0))
        row = self.con.execute("SELECT state,error_class FROM jobs WHERE id=?", (j["id"],)).fetchone()
        self.assertEqual(row["state"], "error")
        self.assertEqual(row["error_class"], "INSTANCE_BUSY")

    def test_enqueue_error_row_dedup_no_crash(self):
        # job en échec terminal (error) re-scanné → dédup, PAS de crash UNIQUE
        # (régression : le poller bouclait en erreur à chaque scan).
        qdb.enqueue(self.con, "R/a", 3, "b", "h9")
        j = qdb.claim_next(self.con)
        qdb.mark(self.con, j["id"], "error", error_class="INTERNAL_ERROR",
                 last_error="test")
        created, row = qdb.enqueue(self.con, "R/a", 3, "b", "h9")
        self.assertFalse(created)
        self.assertEqual(row["state"], "error")
        # un SHA différent, même PR → nouveau job autorisé
        created2, _ = qdb.enqueue(self.con, "R/a", 3, "b", "h10")
        self.assertTrue(created2)


class TestMapping(unittest.TestCase):
    def test_verdict_status(self):
        self.assertEqual(wk.verdict_status({"status": "PASS", "model_certified": True}), "PASS")
        self.assertEqual(wk.verdict_status({"status": "BLOCK", "model_certified": True}), "BLOCK")
        self.assertEqual(wk.verdict_status({"status": "PASS", "model_certified": False}), "REVIEW_UNAVAILABLE")
        self.assertEqual(wk.verdict_status({"status": "REVIEW_UNAVAILABLE", "model_certified": False}), "REVIEW_UNAVAILABLE")

    def test_status_for_verdict(self):
        # commit status : jamais success sans certification
        self.assertEqual(wk.status_for_verdict({"status": "PASS", "model_certified": True})[0], "success")
        self.assertEqual(wk.status_for_verdict({"status": "BLOCK", "model_certified": True})[0], "failure")
        self.assertEqual(wk.status_for_verdict({"status": "PASS", "model_certified": False})[0], "error")
        self.assertEqual(wk.status_for_verdict({"status": "REVIEW_UNAVAILABLE", "model_certified": False})[0], "error")


class TestPoller(unittest.TestCase):
    """scan_once : gate fork/draft + dédup, sans réseau (github_client stubé)."""

    def setUp(self):
        import types
        self.dbp = pathlib.Path(TMP) / f"poll{time.time_ns()}.db"
        self.con = qdb.connect(self.dbp)
        fake = types.ModuleType("github_client")
        self.pulls = []
        fake.list_open_pulls = lambda repo: self.pulls
        self._orig = sys.modules.get("github_client")
        sys.modules["github_client"] = fake
        self.cfg = dict(json.loads(os.environ["TRANSPORT_CONFIG"]))
        self.cfg["repos"] = ["Revens2/agent-island"]
        os.environ["TRANSPORT_CONFIG"] = json.dumps(self.cfg)
        # re-import du poller avec le module stubé
        for m in list(sys.modules):
            if m == "poller" or m.startswith("poller."):
                del sys.modules[m]
        import poller as pl
        self.pl = pl

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = INITIAL_TRANSPORT_CONFIG
        if self._orig:
            sys.modules["github_client"] = self._orig
        else:
            sys.modules.pop("github_client", None)
        self.con.close()

    def _pr(self, n, sha, base="b" * 40, repo="Revens2/agent-island", draft=False,
            ref="test/pr-reviewer-b", assoc="OWNER"):
        return {"number": n, "draft": draft, "title": "T",
                "author_association": assoc,
                "head": {"sha": sha, "ref": ref, "repo": {"full_name": repo}},
                "base": {"sha": base}}

    def test_same_repo_only_and_dedup(self):
        self.pulls = [
            self._pr(1, "h" * 40),
            self._pr(2, "j" * 40, repo="SomeoneElse/fork"),   # fork → jamais
            self._pr(3, "k" * 40, draft=True),                 # draft hors repo test
        ]
        self.pl.REPOS = self.cfg["repos"]
        self.pl.DRAFT_REPOS = set()
        c, d, f, dr, a, p, e = self.pl.scan_once(self.con)
        self.assertEqual((c, f, dr), (1, 1, 1))
        # re-scan → dédup (pas de 2e job)
        c2, d2, *_ = self.pl.scan_once(self.con)
        self.assertEqual((c2, d2), (0, 1))
        row = self.con.execute("SELECT repo,pr,head_sha,state FROM jobs").fetchall()
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["head_sha"], "h" * 40)

    def test_draft_allowed_on_test_repo(self):
        self.pulls = [self._pr(7, "q" * 40, draft=True)]
        self.pl.REPOS = self.cfg["repos"]
        self.pl.DRAFT_REPOS = {"Revens2/agent-island"}
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 1)

    def test_branch_prefix_gate(self):
        # mode pilote : seules les branches de test sont reviewées
        self.pulls = [
            self._pr(10, "r" * 40, ref="fix/island-layout"),
            self._pr(11, "s" * 40, ref="test/pr-reviewer-b"),
        ]
        self.pl.REPOS = self.cfg["repos"]
        self.pl.DRAFT_REPOS = set()
        self.pl.BRANCH_PREFIXES = ["test/pr-reviewer-"]
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 1)
        row = self.con.execute("SELECT pr,head_sha FROM jobs").fetchone()
        self.assertEqual(row["pr"], 11)

    def test_author_gate_skips_non_collaborators(self):
        self.pulls = [
            self._pr(12, "t" * 40, assoc="CONTRIBUTOR"),   # externe → jamais
            self._pr(13, "u" * 40, assoc="OWNER"),
            self._pr(14, "v" * 40, assoc="MEMBER"),
        ]
        self.pl.REPOS = self.cfg["repos"]
        self.pl.DRAFT_REPOS = set()
        self.pl.AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
        c, d, f, dr, a, p, e = self.pl.scan_once(self.con)
        self.assertEqual((c, a), (2, 1))

    def test_advisory_no_prefix_reviews_all_same_repo(self):
        # mode advisory réel : préfixe vide → toutes les branches internes
        self.pulls = [self._pr(20, "w" * 40, ref="feat/whatever")]
        self.pl.REPOS = self.cfg["repos"]
        self.pl.DRAFT_REPOS = set()
        self.pl.BRANCH_PREFIXES = []
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 1)


class TestPollerMultiRepo(unittest.TestCase):
    """scan_once multi-repo : allowlist explicite, isolation repo|PR|SHA, gates
    fork/draft appliqués par repo. Repos neutres (aucune hypothèse agent-island).
    Aucun réseau : github_client stubé, pulls par repo."""

    REPOS = ["Revens2/pr-reviewer", "Revens2/homelab-ops"]

    def setUp(self):
        import types
        self.dbp = pathlib.Path(TMP) / f"mr{time.time_ns()}.db"
        self.con = qdb.connect(self.dbp)
        fake = types.ModuleType("github_client")
        self.by_repo = {r: [] for r in self.REPOS}
        self.queries = []
        fake.list_open_pulls = lambda repo: (self.queries.append(repo) or self.by_repo.get(repo, []))
        self._orig = sys.modules.get("github_client")
        sys.modules["github_client"] = fake
        self.cfg = dict(json.loads(os.environ["TRANSPORT_CONFIG"]))
        self.cfg["repos"] = list(self.REPOS)
        self.cfg["draft_repos"] = []
        os.environ["TRANSPORT_CONFIG"] = json.dumps(self.cfg)
        for m in list(sys.modules):
            if m == "poller" or m.startswith("poller."):
                del sys.modules[m]
        import poller as pl
        self.pl = pl

    def tearDown(self):
        os.environ["TRANSPORT_CONFIG"] = INITIAL_TRANSPORT_CONFIG
        if self._orig:
            sys.modules["github_client"] = self._orig
        else:
            sys.modules.pop("github_client", None)
        self.con.close()

    def _pr(self, repo, n, sha, head_repo=None, draft=False, ref="feat/x", assoc="OWNER"):
        head_repo = head_repo or repo
        return {"number": n, "draft": draft, "title": "T",
                "author_association": assoc,
                "head": {"sha": sha, "ref": ref, "repo": {"full_name": head_repo}},
                "base": {"sha": "b" * 40}}

    def test_allowlist_only_configured_repos_queried(self):
        self.by_repo["Revens2/pr-reviewer"] = [self._pr("Revens2/pr-reviewer", 1, "a" * 40)]
        # PR ouverte sur un repo NON configuré : jamais requêté, jamais enqueue
        self.by_repo["Revens2/homelab-ops"] = []
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 1)
        self.assertEqual(sorted(self.queries), sorted(self.REPOS))
        rows = self.con.execute("SELECT repo,pr FROM jobs").fetchall()
        self.assertEqual([r["repo"] for r in rows], ["Revens2/pr-reviewer"])

    def test_same_pr_number_across_two_repos_isolated(self):
        # même numéro de PR (42) sur deux repos, SHAs différents → 2 jobs séparés
        self.by_repo["Revens2/pr-reviewer"] = [self._pr("Revens2/pr-reviewer", 42, "a" * 40)]
        self.by_repo["Revens2/homelab-ops"] = [self._pr("Revens2/homelab-ops", 42, "f" * 40)]
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 2)
        rows = self.con.execute("SELECT id,repo,pr,head_sha FROM jobs ORDER BY repo").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["repo"], "Revens2/homelab-ops")
        self.assertEqual(rows[1]["repo"], "Revens2/pr-reviewer")
        self.assertEqual(rows[0]["pr"], 42)
        self.assertEqual(rows[1]["pr"], 42)
        self.assertEqual(rows[0]["head_sha"], "f" * 40)
        self.assertEqual(rows[1]["head_sha"], "a" * 40)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        # re-scan → dédup (rien de nouveau)
        c2, d2, *_ = self.pl.scan_once(self.con)
        self.assertEqual((c2, d2), (0, 2))

    def test_fork_and_draft_skipped_on_every_repo(self):
        self.by_repo["Revens2/pr-reviewer"] = [
            self._pr("Revens2/pr-reviewer", 10, "a" * 40),
            # PR depuis un fork externe → jamais de review
            self._pr("Revens2/pr-reviewer", 11, "b" * 40, head_repo="SomeoneElse/fork"),
        ]
        self.by_repo["Revens2/homelab-ops"] = [
            self._pr("Revens2/homelab-ops", 12, "c" * 40, draft=True),   # draft → SKIP
            self._pr("Revens2/homelab-ops", 13, "d" * 40),
        ]
        c, d, f, dr, a, p, e = self.pl.scan_once(self.con)
        self.assertEqual((c, f, dr), (2, 1, 1))
        rows = self.con.execute("SELECT pr,repo FROM jobs ORDER BY pr").fetchall()
        self.assertEqual([r["pr"] for r in rows], [10, 13])

    def test_unconfigured_repo_skipped_even_if_pull_listed(self):
        # le stub ne reçoit JAMAIS d'appel pour un repo hors allowlist
        self.by_repo = {r: [self._pr(r, 1, "a" * 40)] for r in self.REPOS}
        self.by_repo["Revens2/unknown"] = [self._pr("Revens2/unknown", 1, "z" * 40)]
        c, *_ = self.pl.scan_once(self.con)
        self.assertEqual(c, 2)
        self.assertNotIn("Revens2/unknown", self.queries)
        rows = self.con.execute("SELECT DISTINCT repo FROM jobs").fetchall()
        self.assertEqual(sorted(r["repo"] for r in rows), sorted(self.REPOS))


class TestEnvfile(unittest.TestCase):
    """envfile.load : charge state/.env sans écraser l'env existant."""

    KEYS = ["PRT_TEST_A", "PRT_TEST_B", "PRT_TEST_C"]

    def tearDown(self):
        for k in self.KEYS:
            os.environ.pop(k, None)

    def _write(self, text):
        p = pathlib.Path(TMP) / f"env{time.time_ns()}.env"
        p.write_text(text)
        return p

    def test_loads_export_and_plain(self):
        p = self._write(
            "# commentaire\n"
            "export PRT_TEST_A=hello\n"
            "PRT_TEST_B=\"world x\"\n"
            "\n"
            "PRT_TEST_C='single'\n"
        )
        loaded = envfile.load(p)
        self.assertEqual(os.environ["PRT_TEST_A"], "hello")
        self.assertEqual(os.environ["PRT_TEST_B"], "world x")
        self.assertEqual(os.environ["PRT_TEST_C"], "single")
        self.assertEqual(set(loaded), {"PRT_TEST_A", "PRT_TEST_B", "PRT_TEST_C"})

    def test_never_overrides_existing_env(self):
        os.environ["PRT_TEST_A"] = "already-set"
        p = self._write("export PRT_TEST_A=from-file\n")
        loaded = envfile.load(p)
        self.assertEqual(os.environ["PRT_TEST_A"], "already-set")
        self.assertEqual(loaded, {})

    def test_missing_file_is_noop(self):
        self.assertEqual(envfile.load(pathlib.Path(TMP) / "absent.env"), {})


class TestFreebucks(unittest.TestCase):
    """classify_freebucks / parse_balance : politique quota pure, sans UI live."""

    def test_parse_session_footer(self):
        from worker import parse_balance
        self.assertEqual(parse_balance("Session ended · 30 Freebucks left"), (30, None))
        self.assertEqual(parse_balance("Muse Spark 1.3 · 8m left · 77.5K (59%)"),
                         (None, 8))
        self.assertEqual(parse_balance("rien à voir"), (None, None))

    def test_exhausted_on_zero_or_wording(self):
        from worker import classify_freebucks
        self.assertEqual(classify_freebucks("0 Freebucks left")[0], "EXHAUSTED")
        self.assertEqual(classify_freebucks("Add Freebucks to continue")[0], "EXHAUSTED")
        self.assertEqual(classify_freebucks("out of Freebucks")[0], "EXHAUSTED")

    def test_low_threshold(self):
        from worker import classify_freebucks
        self.assertEqual(classify_freebucks("5 Freebucks left", low_threshold=10)[0], "LOW")
        self.assertEqual(classify_freebucks("5 Freebucks left", low_threshold=3)[0], "OK")
        self.assertEqual(classify_freebucks("30 Freebucks left", low_threshold=10)[0], "OK")
        self.assertEqual(classify_freebucks("pas de solde affiché")[0], None)

    def test_error_mapping_never_success(self):
        from worker import error_status_description
        for cls in ("AUTH_REQUIRED", "FREEBUCKS_EXHAUSTED", "TIMEOUT",
                    "TIMEOUT_SESSION", "MODEL_UNVERIFIED", "INTERNAL_ERROR"):
            self.assertTrue(error_status_description(cls).startswith("ERROR"))


class TestReconcile(unittest.TestCase):
    """reconcile_running : running orphelin (crash) → pending si head actuel,
    error STALE_SHA sinon ; requeue optimiste si GitHub injoignable."""

    def setUp(self):
        import worker as wk
        self.wk = wk
        self.dbp = pathlib.Path(TMP) / f"rec{time.time_ns()}.db"
        self.con = qdb.connect(self.dbp)

    def tearDown(self):
        self.con.close()

    def _running(self, repo, pr, head):
        qdb.enqueue(self.con, repo, pr, "b" * 40, head)
        return qdb.claim_next(self.con)  # passe en running

    def test_requeue_current_head_and_stale(self):
        j1 = self._running("R/a", 1, "h1")      # head toujours h1 → pending
        j2 = self._running("R/a", 2, "h2")      # head changé → STALE_SHA

        def fake_head(repo, pr):
            return "h1" if pr == 1 else "h3"

        self.wk.reconcile_running(self.con, get_head=fake_head)
        st1 = self.con.execute("SELECT state FROM jobs WHERE pr=1").fetchone()["state"]
        st2 = self.con.execute("SELECT state,error_class FROM jobs WHERE pr=2").fetchone()
        self.assertEqual(st1, "pending")
        self.assertEqual(st2["state"], "error")
        self.assertEqual(st2["error_class"], "STALE_SHA")

    def test_optimistic_requeue_when_github_down(self):
        j = self._running("R/a", 3, "h4")

        def boom(repo, pr):
            raise RuntimeError("github down")

        self.wk.reconcile_running(self.con, get_head=boom)
        st = self.con.execute("SELECT state FROM jobs WHERE pr=3").fetchone()["state"]
        self.assertEqual(st, "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
