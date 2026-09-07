#!/usr/bin/env bash
# run_review.sh — Orchestrateur d'une review (HÔTE). Prépare le snapshot read-only,
# lance un conteneur jetable durci, récupère le verdict, assemble la certification.
#
# Usage :
#   run_review.sh --repo owner/name --pr 123 --head-sha <sha> --snapshot-dir <dir>
# Env : FB_IMAGE, FB_TIMEOUT_S, FB_MODEL_TARGET, FB_VOL_AUTH, FB_VOL_OUT, FB_DRY=1
set -u
REPO=""; PR=""; HEAD_SHA=""; SNAP=""; DRY="${FB_DRY:-0}"
IMAGE="${FB_IMAGE:-freebuff-reviewer:0.1.0}"
OUT_BASE="${OUT_BASE:-/tmp/fb-reviewer-out}"
MODEL_REQUESTED="${FB_MODEL_TARGET:-meta/muse-spark-1.3-contributor}"
TIMEOUT_S="${FB_TIMEOUT_S:-900}"
VOL_AUTH="${FB_VOL_AUTH:-freebuff-reviewer-auth}"
VOL_OUT="${FB_VOL_OUT:-freebuff-reviewer-output}"
UID_GID="${FB_UID_GID:-3741:3741}"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;; --pr) PR="$2"; shift 2;;
    --head-sha) HEAD_SHA="$2"; shift 2;; --snapshot-dir) SNAP="$2"; shift 2;;
    --dry-run) DRY=1; shift;; *) echo "unknown $1" >&2; exit 2;;
  esac
done
[ -n "$REPO" ] && [ -n "$PR" ] && [ -n "$HEAD_SHA" ] && [ -d "$SNAP" ] || {
  echo "usage: run_review.sh --repo R --pr N --head-sha S --snapshot-dir D" >&2; exit 2; }

JOB="$(echo "${REPO}/${PR}/${HEAD_SHA}" | tr '/:' '__')"
RUN_DIR="${OUT_BASE}/${JOB}"
rm -rf "${RUN_DIR}"; mkdir -p "${RUN_DIR}/input"
LOG="${RUN_DIR}/run.log"
exec >>"${LOG}" 2>&1
echo "[$(date -u +%FT%TZ)] job=${JOB} repo=${REPO} pr=${PR} sha=${HEAD_SHA} image=${IMAGE}"

# 1. Snapshot read-only (checkout git du head SHA en production — PAS le code du runner).
cp -a "${SNAP}/." "${RUN_DIR}/input/repository"
printf '{"repository":"%s","pull_request":%s,"base_sha":null,"head_sha":"%s","files_changed":[]}\n' \
  "$REPO" "$PR" "$HEAD_SHA" > "${RUN_DIR}/input/context.json"
chmod -R a-w "${RUN_DIR}/input/repository" 2>/dev/null || true
echo "snapshot prêt (RO): ${RUN_DIR}/input"

if [ "${DRY}" = "1" ]; then echo "DRY RUN"; exit 0; fi

# 2. Verrou global (concurrency=1)
LOCK="${OUT_BASE}/.lock"
exec 9>"${LOCK}"
flock -n 9 || { echo "BUSY — review déjà active"; exit 5; }
echo "lock acquise"

# 3. Conteneur jetable durci : driver + probe modèle chaînés.
START=$(date +%s)
docker run --rm --name "fb-review-${JOB}" --init \
  --read-only \
  --user "${UID_GID}" \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --pids-limit 128 \
  --memory 1536m \
  --cpus 2.0 \
  --tmpfs /tmp:rw,size=256m,mode=1777,exec \
  --tmpfs /home/reviewer/.cache:rw,size=64m,uid=3741,gid=3741 \
  -v "${VOL_AUTH}:/home/reviewer/.config/manicode" \
  -v freebuff-reviewer-state:/home/reviewer/.freebuff \
  -v "${RUN_DIR}/input:/reviewer/pr:ro" \
  -v "${VOL_OUT}:/reviewer/out" \
  -e HOME=/home/reviewer \
  -e TERM=xterm-256color \
  -e FB_MODEL_TARGET="${MODEL_REQUESTED}" \
  -e FB_TIMEOUT_S="${TIMEOUT_S}" \
  "${IMAGE}" \
  bash -lc 'bash /reviewer/orchestrator/tui_driver.sh --job "$FB_JOB" --timeout-s "$FB_TIMEOUT_S"; rc=$?; bash /reviewer/orchestrator/model_probe.sh /reviewer/out/evidence 2>/dev/null; exit $rc' \
  || DRIVER_RC=$?
DRIVER_RC="${DRIVER_RC:-0}"
DURATION=$(( $(date +%s) - START ))
echo "driver rc=${DRIVER_RC} duration=${DURATION}s"
docker run --rm -v "${VOL_OUT}:/out:ro" -v "${RUN_DIR}:/host" "${IMAGE}" \
  bash -lc 'cp -a /out/. /host/ 2>/dev/null; exit 0'

# 4. Assemblage du verdict certifié.
python3 "$(dirname "$0")/assemble_verdict.py" \
  --job-dir "${RUN_DIR}" --driver-rc "${DRIVER_RC}" \
  --repo "$REPO" --pr "$PR" --head-sha "$HEAD_SHA" \
  --model-requested "$MODEL_REQUESTED" > "${RUN_DIR}/verdict.json" 2> "${RUN_DIR}/assemble.err" \
  || echo "assemble_verdict failed (voir assemble.err)"
cat "${RUN_DIR}/verdict.json" 2>/dev/null
exit 0
