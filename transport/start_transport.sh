#!/usr/bin/env bash
# Démarre poller + receiver + worker (concurrency=1) — orchestrateur GitHub.
set -a; source "$(dirname "$0")/state/.env"; set +a
if [ -z "${GH_TRANSPORT_TOKEN:-}" ]; then
  echo "FATAL: GH_TRANSPORT_TOKEN absent/ vide dans state/.env — refus de démarrer" >&2
  exit 1
fi
cd "$(dirname "$0")"
mkdir -p state/logs state/jobs
for p in poller receiver worker; do
  if [ -f "state/$p.pid" ] && kill -0 "$(cat "state/$p.pid")" 2>/dev/null; then :; else
    setsid nohup python3 "$p.py" >> "state/logs/$p.log" 2>&1 &
    echo $! > "state/$p.pid"
  fi
done
sleep 1
echo "poller pid=$(cat state/poller.pid 2>/dev/null) receiver pid=$(cat state/receiver.pid 2>/dev/null) worker pid=$(cat state/worker.pid 2>/dev/null)"
