#!/usr/bin/env python3
"""github_client.py — accès GitHub REST côté ORCHESTRATEUR uniquement.

Le token (PAT ou GitHub App) ne quitte jamais ce processus ; il n'est JAMAIS
passé au conteneur Freebuff. Endpoints utilisés :
  - GET  /repos/{r}/pulls/{n}            (head SHA actuel, state, draft)
  - GET  /repos/{r}/compare/{base}...{head}  (diff pour le snapshot)
  - POST /repos/{r}/check-runs           (publish check)
  - GET  /repos/{r}/pulls/{n}/comments   (liste pour remplacement anti-spam)
  - POST /repos/{r}/pulls/{n}/comments   (commentaire BLOCK/synthèse)
"""
import base64, json, os, time, urllib.request, urllib.error, pathlib, subprocess

API = "https://api.github.com"


def _token():
    t = os.environ.get("GH_TRANSPORT_TOKEN")
    if t:
        return t
    # GitHub App : (app id + clé) → installation token (cache court).
    app_id = os.environ.get("GH_TRANSPORT_APP_ID")
    key_path = os.environ.get("GH_TRANSPORT_PRIVATE_KEY")
    inst = os.environ.get("GH_TRANSPORT_INSTALLATION_ID")
    if app_id and key_path and inst:
        return _app_installation_token(app_id, key_path, inst)
    raise RuntimeError("GH_TRANSPORT_TOKEN ou GH_TRANSPORT_APP_ID+PRIVATE_KEY+INSTALLATION requis")


def _jwt(app_id, key_pem):
    import jwt as pyjwt  # optionnel (PyJWT) — sinon jeté plus bas
    return pyjwt.encode({"iat": int(time.time()) - 60,
                         "exp": int(time.time()) + 540,
                         "iss": app_id}, key_pem, algorithm="RS256")


def _app_installation_token(app_id, key_path, inst):
    key = pathlib.Path(key_path).read_text()
    try:
        import jwt as pyjwt
        tok = pyjwt.encode({"iat": int(time.time()) - 60, "exp": int(time.time()) + 540,
                            "iss": app_id}, key, algorithm="RS256")
    except ImportError:
        # PyJWT absent : encodage JWT RS256 minimal (stdlib uniquement).
        import hashlib
        h = lambda d: base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b"=")
        def b64(x):
            if isinstance(x, str): x = x.encode()
            return base64.urlsafe_b64encode(x).rstrip(b"=")
        import hmac
        header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}))
        now = int(time.time())
        payload = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}))
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        priv = serialization.load_pem_private_key(key.encode(), password=None)
        sig = priv.sign(header + b"." + payload, padding.PKCS1v15(), hashes.SHA256())
        tok = (header + b"." + payload + b"." + b64(sig)).decode()
    req = urllib.request.Request(
        f"{API}/app/installations/{inst}/access_tokens", data=b"{}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["token"]


def list_open_pulls(repo):
    """PR ouvertes d'un repo (utilisé par le poller)."""
    return _req("GET", f"/repos/{repo}/pulls?state=open&per_page=100")


def _req(method, path, token=None, data=None, timeout=30):
    token = token or _token()
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"{API}{path}", data=body, method=method, headers={
        "Authorization": f"token {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pr-reviewer-transport"})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"github {method} {path} -> {e.code}: {detail}") from e


def get_pr(repo, pr):
    d = _req("GET", f"/repos/{repo}/pulls/{pr}")
    return {"head_sha": d["head"]["sha"], "base_sha": d["base"]["sha"],
            "state": d.get("state"), "draft": d.get("draft", False), "title": d.get("title", "")}


def get_compare(repo, base_sha, head_sha):
    """Diff base...head. Retourne {patch, files, truncated}."""
    try:
        d = _req("GET", f"/repos/{repo}/compare/{base_sha}...{head_sha}")
    except RuntimeError:
        return {"patch": "", "files": [], "truncated": True}
    chunks = []
    for f in d.get("files", []):
        fn = f.get("filename", "")
        status = f.get("status", "modified")
        patch = f.get("patch", "")
        header = f"diff --git a/{fn} b/{fn}\nnew file mode {f.get('raw_url') and '100644' or ''}\n" \
                 if status == "added" else f"diff --git a/{fn} b/{fn}\n"
        head = f.get("previous_filename") or fn
        if status == "renamed":
            header = f"diff --git a/{f.get('previous_filename')} b/{fn}\n"
        if status in ("added", "removed"):
            header = f"diff --git a/{fn} b/{fn}\n"
        meta = f"index {f.get('sha','')[:7]}..{f.get('sha','')[:7]} 100644\n--- a/{fn if status!='added' else '/dev/null'}\n+++ b/{fn if status!='removed' else '/dev/null'}\n"
        chunks.append(header + (meta if patch else "") + (patch or f"(no inline patch — {status})\n"))
    return {"patch": "\n".join(chunks), "files": [f["filename"] for f in d.get("files", [])],
            "truncated": bool(d.get("truncated"))}


def create_status(repo, sha, state, context, description):
    """Commit status (transport sans GitHub App). states: pending|success|failure|error.
    Un commit status s'affiche dans la PR comme contexte de check."""
    data = {"state": state, "context": context,
            "description": (description or "")[:140]}
    d = _req("POST", f"/repos/{repo}/statuses/{sha}", data=data)
    return d.get("id")


def create_check(repo, name, head_sha, conclusion, summary_text, details_url=None):
    """conclusion: success | failure | neutral (advisory). Une seule conclusion par SHA."""
    data = {"name": name, "head_sha": head_sha, "status": "completed",
            "conclusion": conclusion,
            "output": {"title": f"{name}: {conclusion.upper()}",
                       "summary": (summary_text or "")[:64000]}}
    if details_url:
        data["details_url"] = details_url
    d = _req("POST", f"/repos/{repo}/check-runs", data=data)
    return d.get("id")


def list_comments(repo, pr, marker="muse-semantic-review"):
    """Commentaires de PR (endpoint issues — les PR SONT des issues)."""
    d = _req("GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100")
    return [c for c in d if marker in (c.get("body") or "")]
def delete_comment(repo, comment_id):
    _req("DELETE", f"/repos/{repo}/issues/comments/{comment_id}")
