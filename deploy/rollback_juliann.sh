#!/usr/bin/env bash
# ROLLBACK durcissement pr-reviewer → état précédent (User=juliann, Group=docker).
# Exécuter en ROOT via docker nsenter :
#   docker run --rm --privileged --pid=host alpine nsenter -t 1 -m -u -i -n \
#     bash /home/juliann/pr-reviewer-20260906-deploy/rollback_juliann.sh
set -euo pipefail
DEP=/home/juliann/pr-reviewer-20260906-deploy
BK="$DEP/backup-units-juliann"
if [ ! -f "$BK/pr-reviewer-worker.service" ]; then
  echo "backup absent ($BK) — rien à restaurer"; exit 1
fi
cp "$BK/pr-reviewer-poller.service" "$BK/pr-reviewer-worker.service" /etc/systemd/system/
rm -f /etc/sudoers.d/pr-reviewer
systemctl daemon-reload
systemctl restart pr-reviewer-poller.service pr-reviewer-worker.service
sleep 3
echo "active: $(systemctl is-active pr-reviewer-poller) / $(systemctl is-active pr-reviewer-worker)"
echo "ROLLBACK_OK (le wrapper et l'utilisateur prreview restent présents mais inutilisés)"
