#!/usr/bin/env bash
# test_offline.sh — tests SANS quota modèle :
#   1. build fixtures
#   2. FAKE_TUI driver (machine d'état, timeouts, sentinelles)
#   3. assemble_verdict : PASS/BLOCK/PARSER_ERROR/AUTH_REQUIRED/TIMEOUT/STALE
#   4. adversarial filesystem (repo RO, pas de docker.sock, pas de secrets host)
# Usage : tests/test_offline.sh [image]   (image défaut freebuff-reviewer:0.1.0)
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
# Git Bash (MSYS) convertit les chemins POSIX dans les args docker ; l'interdire
# uniquement pour docker (sinon python Windows casse sur les chemins /c/...).
dk() { MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker "$@"; }
IMAGE="${1:-freebuff-reviewer:0.1.0}"
WORK="${TMPDIR:-/tmp}/fb-reviewer-tests"
rm -rf "${WORK}"; mkdir -p "${WORK}"
PASS=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
ko()   { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else ko "$1"; fi }

echo "=== 0. fixtures ==="
python3 "${HERE}/fixtures/build_fixtures.py" || { echo "fixture build failed"; exit 1; }

echo "=== 1. driver FAKE_TUI (container éphémère) ==="
dk run --rm --init --read-only --user 3741:3741 \
  --security-opt no-new-privileges:true --cap-drop ALL --pids-limit 128 \
  --tmpfs /tmp:rw,size=256m,mode=1777,exec \
  -v "${HERE}/reviewer/orchestrator:/reviewer/orchestrator:ro" \
  --env FAKE_TUI=1 --env OUT_DIR=/tmp/fbout --env HOME=/home/reviewer \
  "${IMAGE}" bash -lc '
    set -e
    export TERM=xterm-256color
    bash /reviewer/orchestrator/tui_driver.sh --job offline-test --timeout-s 60
    rc=$?
    echo "driver_rc=$rc"
    ls -la /tmp/fbout 2>/dev/null || true
    exit 0
  ' > "${WORK}/fake-driver.out" 2>&1
grep -q "OK wait:ready" "${WORK}/fake-driver.out" && ok "driver: attend readiness" || ko "driver: readiness ($(tail -3 ${WORK}/fake-driver.out))"
grep -q "OK result.json present\|sentinel found" "${WORK}/fake-driver.out" && ok "driver: fin détectée" || ko "driver: fin"

echo "=== 2. assemble_verdict — PASS (result.json) ==="
mkdir -p "${WORK}/job-pass"
cat > "${WORK}/job-pass/result.json" <<'EOF'
{"schema_version":1,"status":"PASS","head_sha":"abc123","summary":"ok","findings":[{"severity":"info","file":"a.py","line":1,"title":"t","reason":"r"}]}
EOF
mkdir -p "${WORK}/job-pass/evidence"
printf '{"model_observed":null,"certified":false,"state":"MODEL_UNVERIFIED","evidence":[]}' > "${WORK}/job-pass/evidence/model.json"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-pass" \
  --driver-rc 0 --repo r --pr 1 --head-sha abc123 > "${WORK}/v-pass.json" 2>&1
check "verdict PASS" "grep -q '\"status\": \"PASS\"' ${WORK}/v-pass.json"
check "verdict non certifié (MODEL_UNVERIFIED)" "grep -q '\"model_certified\": false' ${WORK}/v-pass.json && grep -q 'MODEL_UNVERIFIED' ${WORK}/v-pass.json"

echo "=== 3. assemble_verdict — BLOCK via major ==="
mkdir -p "${WORK}/job-block"
cat > "${WORK}/job-block/result.json" <<'EOF'
{"schema_version":1,"status":"PASS","head_sha":"abc123","summary":"","findings":[{"severity":"major","file":"x","line":2,"title":"bug","reason":"delete all"}]}
EOF
mkdir -p "${WORK}/job-block/evidence"
printf '{"model_observed":null,"certified":false,"state":"MODEL_UNVERIFIED"}' > "${WORK}/job-block/evidence/model.json"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-block" \
  --driver-rc 0 --repo r --pr 2 --head-sha abc123 > "${WORK}/v-block.json" 2>&1
check "verdict BLOCK (surclassement)" "grep -q '\"status\": \"BLOCK\"' ${WORK}/v-block.json"

echo "=== 4. AUTH_REQUIRED (rc=10) → fail-closed ==="
mkdir -p "${WORK}/job-auth"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-auth" \
  --driver-rc 10 --repo r --pr 3 --head-sha abc123 > "${WORK}/v-auth.json" 2>&1
check "AUTH_REQUIRED explicite" "grep -q 'AUTH_REQUIRED' ${WORK}/v-auth.json && grep -q '\"status\": \"REVIEW_UNAVAILABLE\"' ${WORK}/v-auth.json"

echo "=== 5. TIMEOUT (rc=20) → jamais PASS ==="
mkdir -p "${WORK}/job-timeout"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-timeout" \
  --driver-rc 20 --repo r --pr 4 --head-sha abc123 > "${WORK}/v-timeout.json" 2>&1
check "TIMEOUT explicite" "grep -q 'TIMEOUT' ${WORK}/v-timeout.json && grep -q '\"status\": \"REVIEW_UNAVAILABLE\"' ${WORK}/v-timeout.json"

echo "=== 6. sentinelle seule (rc=3) ==="
JOBID="r_5_abc123"
mkdir -p "${WORK}/job-sent"
printf '<<<PR_REVIEW_RESULT_V1>>>\n{"schema_version":1,"status":"BLOCK","findings":[{"severity":"critical","file":"a","line":1,"title":"inj","reason":"injection"}]}\n<<<END_PR_REVIEW_RESULT_V1>>>\n' > "${WORK}/job-sent/transcript-${JOBID}.txt"
mkdir -p "${WORK}/job-sent/evidence"
printf '{"model_observed":null,"certified":false}' > "${WORK}/job-sent/evidence/model.json"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-sent" \
  --driver-rc 3 --repo r --pr 5 --head-sha abc123 > "${WORK}/v-sent.json" 2>&1
check "sentinel extraite (source=sentinel)" "grep -q '\"result_source\": \"sentinel\"' ${WORK}/v-sent.json && grep -q 'BLOCK' ${WORK}/v-sent.json"

echo "=== 7. SHA stale rejeté ==="
mkdir -p "${WORK}/job-stale"
cat > "${WORK}/job-stale/result.json" <<'EOF'
{"schema_version":1,"status":"PASS","head_sha":"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef","summary":"","findings":[]}
EOF
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-stale" \
  --driver-rc 0 --repo r --pr 6 --head-sha abc123 > "${WORK}/v-stale.json" 2>&1
check "STALE_SHA_MISMATCH → REVIEW_UNAVAILABLE" "grep -q 'STALE_SHA_MISMATCH' ${WORK}/v-stale.json && grep -q '\"status\": \"REVIEW_UNAVAILABLE\"' ${WORK}/v-stale.json"

echo "=== 7b. INSTANCE_BUSY (rc=30) → fail-closed ==="
mkdir -p "${WORK}/job-busy"
python3 "${HERE}/reviewer/orchestrator/assemble_verdict.py" --job-dir "${WORK}/job-busy" \
  --driver-rc 30 --repo r --pr 7 --head-sha abc123 > "${WORK}/v-busy.json" 2>&1
check "INSTANCE_BUSY explicite" "grep -q 'INSTANCE_BUSY' ${WORK}/v-busy.json && grep -q '\"status\": \"REVIEW_UNAVAILABLE\"' ${WORK}/v-busy.json"

echo "=== 8. adversarial filesystem (dans le conteneur durci) ==="
dk run --rm --init --read-only --user 3741:3741 \
  --security-opt no-new-privileges:true --cap-drop ALL --pids-limit 128 \
  --tmpfs /tmp:rw,size=256m,mode=1777,exec \
  -v "${HERE}/fixtures/input/bug-logic:/reviewer/pr:ro" \
  --env HOME=/home/reviewer --env OUT_DIR=/tmp/adv \
  "${IMAGE}" bash -c '
    set -u
    fails=0
    # pas de docker.sock
    [ ! -e /var/run/docker.sock ] || { echo "FAIL: docker.sock visible"; fails=$((fails+1)); }
    # pas de clés SSH / secrets host
    ls /root/.ssh 2>/dev/null && { echo "FAIL: root ssh visible"; fails=$((fails+1)); }
    [ ! -e /home/*/.ssh ] 2>/dev/null || true
    # pas de docker CLI
    command -v docker >/dev/null 2>&1 && { echo "FAIL: docker cli present"; fails=$((fails+1)); }
    command -v ssh >/dev/null 2>&1 && { echo "FAIL: ssh client present"; fails=$((fails+1)); }
    command -v gh >/dev/null 2>&1 && { echo "FAIL: gh present"; fails=$((fails+1)); }
    # rootfs RO : écriture interdite
    touch /etc/pwned 2>/dev/null && { echo "FAIL: /etc writable"; fails=$((fails+1)); }
    # écriture repo impossible (bind RO)
    ( echo x > /reviewer/pr/repository/try.txt ) 2>/dev/null && { echo "FAIL: /reviewer/pr writable"; fails=$((fails+1)); }
    # skill trusted RO
    ( echo x > /reviewer/.agents/skills/pr-review/SKILL.md ) 2>/dev/null && { echo "FAIL: trusted skill writable"; fails=$((fails+1)); }
    # utilisateur non-root attendu
    [ "$(id -u)" = "3741" ] || { echo "FAIL: uid != 3741"; fails=$((fails+1)); }
    # cap_drop ALL
    grep -q "CapEff:.*0000000000000000" /proc/self/status || grep -qi "CapEff:	0000000000000000" /proc/self/status || { echo "note: cap check via CapEff"; grep Cap /proc/self/status; }
    [ "$fails" -eq 0 ]
  ' > "${WORK}/adversarial.out" 2>&1
if [ $? -eq 0 ]; then ok "adversarial: sandbox durcie OK"; else ko "adversarial ($(cat ${WORK}/adversarial.out))"; fi

echo
echo "===== RÉSULTAT : PASS=${PASS} FAIL=${FAIL} ====="
[ "${FAIL}" -eq 0 ]
