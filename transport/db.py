#!/usr/bin/env python3
"""db.py — file SQLite (concurrency=1) : états pending/running/done/error/retry.

job id immuable = repo|pr|head_sha  (dédup natif via PRIMARY KEY).
"""
import json, sqlite3, time, pathlib, os

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  repo TEXT NOT NULL,
  pr INTEGER NOT NULL,
  base_sha TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  title TEXT DEFAULT '',
  state TEXT NOT NULL DEFAULT 'pending',   -- pending|running|retry|done|error
  retries INTEGER NOT NULL DEFAULT 0,
  next_run REAL DEFAULT 0,                  -- epoch s ; 0 = immédiat
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  verdict TEXT,                              -- JSON final (verdict.json)
  error_class TEXT,
  last_error TEXT,
  published INTEGER NOT NULL DEFAULT 0,
  check_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
"""


def connect(db_path):
    db_path = str(db_path)
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.commit()
    return con


def job_id(repo, pr, head_sha):
    return f"{repo}|{pr}|{head_sha}"


def enqueue(con, repo, pr, base_sha, head_sha, title=""):
    """Insert si absent (dédup repo|pr|head_sha).

    Tout état existant (pending/running/retry/done/error) → dédup : jamais de
    crash UNIQUE ni de re-run automatique d'un SHA déjà traité ou en échec
    terminal. Un job en error ne repart qu'après reset/delete opérationnel.
    Retourne (created: bool, row)."""
    jid = job_id(repo, pr, head_sha)
    now = time.time()
    cur = con.execute(
        "SELECT * FROM jobs WHERE id=? AND state IN ('pending','running','retry','done','error')",
        (jid,))
    existing = cur.fetchone()
    if existing:
        return False, existing
    con.execute(
        "INSERT INTO jobs (id,repo,pr,base_sha,head_sha,title,state,created_at) VALUES (?,?,?,?,?,?,'pending',?)",
        (jid, repo, pr, base_sha, head_sha, title, now))
    con.commit()
    return True, con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()


def claim_next(con, now=None, allowed_states=("pending", "retry")):
    """Un seul worker : prend le plus ancien job exécutable. Retourne row ou None."""
    now = time.time() if now is None else now
    rows = con.execute(
        "SELECT * FROM jobs WHERE state IN (%s) AND next_run<=? ORDER BY created_at LIMIT 1"
        % ",".join("?" * len(allowed_states)),
        (*allowed_states, now)).fetchall()
    if not rows:
        return None
    j = rows[0]
    con.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?", (now, j["id"]))
    con.commit()
    return con.execute("SELECT * FROM jobs WHERE id=?", (j["id"],)).fetchone()


def mark(con, jid, state, **fields):
    fields["state"] = state
    if state == "done":
        fields["finished_at"] = time.time()
    elif state == "error":
        fields["finished_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))
    con.commit()


def schedule_retry(con, jid, error_class, error, max_retries, base_delay=60):
    """INSTANCE_BUSY/timeout transitoires → retry différé borné. Jamais de boucle infinie."""
    j = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    retries = (j["retries"] if j else 0) + 1
    if retries > max_retries:
        mark(con, jid, "error", error_class=error_class, last_error=error[:400])
        return False
    delay = base_delay * (2 ** (retries - 1))
    con.execute("UPDATE jobs SET state='retry', retries=?, next_run=?, last_error=?, error_class=? WHERE id=?",
                (retries, time.time() + delay, error[:400], error_class, jid))
    con.commit()
    return True
