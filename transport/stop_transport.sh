#!/usr/bin/env bash
TR="$(cd "$(dirname "$0")" && pwd)"
for p in poller receiver worker; do
  [ -f "$TR/state/$p.pid" ] && kill "$(cat "$TR/state/$p.pid")" 2>/dev/null && rm -f "$TR/state/$p.pid"
done
echo stopped
