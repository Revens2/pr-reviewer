#!/usr/bin/env bash
# HARDENING pr-reviewer (2026-09-07, v2 sans ACL) — exécuté en ROOT via docker nsenter (host).
#  1. utilisateur/groupe système `prreview` (nologin, SANS docker group) ;
#  2. permissions : /home/juliann o+x (traverse), state + fixtures/input → prreview:prreview
#     (2775/664, groupe prreview) ; juliann (ajouté au groupe prreview) garde l'accès ops ;
#  3. wrapper ROOT /usr/local/sbin/pr-reviewer-docker + guard /usr/local/lib (root:root) ;
#  4. sudoers NOPASSWD limité au wrapper (prreview + juliann) ;
#  5. unités systemd durcies (User=prreview, Group=prreview) — worker sans NoNewPrivileges
#     (sudo→wrapper), poller avec NoNewPrivileges (aucun sudo) ;
#  6. validations (id, wrapper inspect/exec, DENY).
# Rollback : deploy/rollback_juliann.sh (unités juliann + groupe docker) — voir docs/OPERATIONS.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DEP=/home/juliann/pr-reviewer-20260906-deploy
TR=/home/juliann/pr-reviewer/transport
U=prreview

echo "== 0. stop anciens services =="
systemctl stop pr-reviewer-poller.service pr-reviewer-worker.service 2>/dev/null || true
sleep 1

echo "== 1. utilisateur/groupe =="
getent group "$U" >/dev/null || groupadd -r "$U"
id -u "$U" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -M -g "$U" "$U"
usermod -a -G "$U" juliann || true
id "$U"

echo "== 2. permissions (sans ACL : chmod + groupe prreview) =="
# traverse du home pour le service (liste les noms uniquement, pas le contenu)
chmod o+x /home/juliann
chmod o+x /home/juliann/pr-reviewer
# runtime mutable → propriété prreview (groupe prreview = juliann conserve l'accès ops)
chown -R "$U:$U" "$TR/state"
find "$TR/state" -type d -exec chmod 2775 {} +
find "$TR/state" -type f ! -name '.env' ! -name 'systemd.env' -exec chmod 664 {} +
chmod 660 "$TR/state/.env" "$TR/state/systemd.env"
chown -R "$U:$U" /home/juliann/pr-reviewer/fixtures/input
find /home/juliann/pr-reviewer/fixtures/input -type d -exec chmod 2775 {} +
echo "  state/fixtures perms ok (prreview:prreview, juliann via groupe prreview)"

echo "== 3. wrapper + guard (root-only) =="
install -o root -g root -m 0755 "$DEP/root-wrapper/pr-reviewer-docker" /usr/local/sbin/pr-reviewer-docker
install -o root -g root -m 0644 "$TR/docker_guard.py" /usr/local/lib/pr_reviewer_docker_guard.py
ls -l /usr/local/sbin/pr-reviewer-docker /usr/local/lib/pr_reviewer_docker_guard.py

echo "== 4. sudoers (wrapper exact uniquement) =="
grep -q '^#includedir /etc/sudoers.d' /etc/sudoers || echo "ATTENTION: pas de #includedir sudoers.d"
cat > /etc/sudoers.d/pr-reviewer <<'SUDO'
prreview ALL=(root) NOPASSWD: /usr/local/sbin/pr-reviewer-docker
juliann ALL=(root) NOPASSWD: /usr/local/sbin/pr-reviewer-docker
SUDO
chmod 440 /etc/sudoers.d/pr-reviewer
visudo -c

echo "== 5. unités durcies =="
cp "$DEP/units/pr-reviewer-poller.service" "$DEP/units/pr-reviewer-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable pr-reviewer-poller.service pr-reviewer-worker.service >/dev/null 2>&1 || true
systemctl start pr-reviewer-poller.service pr-reviewer-worker.service
sleep 4
echo "active: $(systemctl is-active pr-reviewer-poller) / $(systemctl is-active pr-reviewer-worker)"

echo "== 6. validations =="
echo "-- prreview id (sans docker) --"
su -s /bin/bash "$U" -c 'id'
echo "-- wrapper inspect-running via prreview sudo --"
su -s /bin/bash "$U" -c 'sudo -n /usr/local/sbin/pr-reviewer-docker inspect-running' || { echo WRAPPER_INSPECT_FAIL; exit 1; }
echo "-- wrapper exec (docker exec fb-vps id) via prreview sudo --"
su -s /bin/bash "$U" -c 'sudo -n /usr/local/sbin/pr-reviewer-docker exec id' || { echo WRAPPER_EXEC_FAIL; exit 1; }
echo "-- wrapper DENY (rm fb-vps → refus attendu) --"
if su -s /bin/bash "$U" -c 'sudo -n /usr/local/sbin/pr-reviewer-docker rm fb-vps' 2>/dev/null; then
  echo UNEXPECTED_ALLOW; exit 1
else
  echo DENIED_OK
fi
echo "HARDENING_OK"
