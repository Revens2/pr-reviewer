#!/usr/bin/env python3
"""receiver.py — endpoint webhook GitHub (stdlib, pas de dépendance).

POST /webhook  (body JSON brut obligatoire)
  En-têtes requis :
    X-GitHub-Event      pull_request
    X-Hub-Signature-256 sha256=<HMAC_SHA256(body, secret)>
  Réponses :
    200  accepté / dédoublonné
    400  payload invalide / event non supporté / draft ignoré
    401  signature absente ou invalide
    413  body trop grand
    500  erreur interne

Traite pull_request.opened|reopened|ready_for_review|synchronize (jamais draft
sauf repo dans draft_repos, pour fixtures de test). Dédup : repo+PR+head_sha.
"""
import hashlib, hmac, json, os, sys, pathlib, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import envfile
envfile.load()
import db as qdb

CFG = json.loads(os.environ.get("TRANSPORT_CONFIG") or (HERE / "config.json").read_text())
SECRET = os.environ.get("WEBHOOK_SECRET", "")
MAX_BODY = int(os.environ.get("WEBHOOK_MAX_BODY", "2_000_000"))
ALLOWED_EVENTS = {"pull_request"}
ALLOWED_ACTIONS = set(CFG.get("events", []))
DRAFT_REPOS = set(CFG.get("draft_repos", []))


def verify_signature(body: bytes, sig_header: str) -> bool:
    if not SECRET or not sig_header:
        return False
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pr-reviewer/1"

    def _reply(self, code, text=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(text)))
        self.end_headers()
        if text and code not in (204,):
            self.wfile.write(text)

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self._reply(code, raw, "application/json")

    def log_message(self, fmt, *args):
        # logs discrets (pas de body)
        pass

    def do_GET(self):
        self._reply(404, b"not found")

    def do_POST(self):
        if self.path != "/webhook":
            self._reply(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            self._reply(413, b"body too large")
            return
        body = self.rfile.read(length)
        event = self.headers.get("X-GitHub-Event", "")
        sig = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, sig):
            self._reply(401, b"invalid signature")
            return
        try:
            payload = json.loads(body)
        except Exception:
            self._reply(400, b"invalid json")
            return
        if event not in ALLOWED_EVENTS:
            self._json(400, {"ok": False, "reason": "event unsupported"})
            return
        action = payload.get("action", "")
        if action not in ALLOWED_ACTIONS:
            self._json(200, {"ok": True, "reason": "action ignored", "action": action})
            return
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name", "")
        head_sha = (pr.get("head") or {}).get("sha", "")
        base_sha = (pr.get("base") or {}).get("sha", "")
        number = pr.get("number")
        if not (repo and number and head_sha and base_sha):
            self._json(400, {"ok": False, "reason": "payload incomplete"})
            return
        if pr.get("draft") and repo not in DRAFT_REPOS:
            self._json(200, {"ok": True, "reason": "draft ignored"})
            return
        if CFG.get("repos") and repo not in CFG["repos"]:
            self._json(200, {"ok": True, "reason": "repo not authorized"})
            return
        try:
            con = qdb.connect(CFG["db_path"])
            created, row = qdb.enqueue(con, repo, number, base_sha, head_sha,
                                       pr.get("title", ""),
                                       head_ref=(pr.get("head") or {}).get("ref", ""),
                                       author_association=pr.get("author_association", ""))
            con.close()
        except Exception as e:
            self._json(500, {"ok": False, "reason": str(e)[:200]})
            return
        self._json(200, {"ok": True, "created": created,
                         "job_id": qdb.job_id(repo, number, head_sha),
                         "repo": repo, "pr": number, "head_sha": head_sha})


def main():
    host, port = CFG["listen_host"], int(CFG["listen_port"])
    if not SECRET:
        print("FATAL: WEBHOOK_SECRET vide — refus de démarrer", flush=True)
        sys.exit(1)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"receiver listening on {host}:{port} (events={sorted(ALLOWED_ACTIONS)})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
