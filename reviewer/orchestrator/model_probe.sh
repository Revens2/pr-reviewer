#!/usr/bin/env bash
# model_probe.sh — preuves observables du modèle réellement servi (post-review).
# Exécuté DANS le conteneur, juste après le driver (concurrency=1).
#
# Primitives (version 0.0.171, calibrées 2026-09-06) :
#   P1. ~/.config/manicode/settings.json        → freebuffModel (SÉLECTION persistée)
#   P2. ~/.config/manicode/projects/*/chats/*/log.jsonl
#       → lignes "Start agent base3-free-muse-spark-1-3 step N (id)"
#       L'agent provisionné par le serveur est nommé d'après le modèle servi.
#       muse-spark → meta/muse-spark-1.3-contributor ; autre nom → fallback présumé.
#   P3. captures TUI (header de session) dans /reviewer/out/transcript-*.txt
#
# Décision de certification (conservatrice) :
#   - settings.freebuffModel == cible  ET  agent log == muse-spark  → MUSE_OK (certified)
#   - agent log != muse-spark (autre modèle)                        → MUSE_FALLBACK (non certifié)
#   - aucune ligne agent exploitable                                → MODEL_UNVERIFIED (non certifié)
set -u
OUT="${1:-/reviewer/out/evidence}"
mkdir -p "${OUT}"
CFG="${HOME}/.config/manicode"
REQ="${FB_MODEL_TARGET:-meta/muse-spark-1.3-contributor}"
EXPECTED_AGENT="muse-spark"
STATE="MODEL_UNVERIFIED"
OBSERVED=""
CERTIFIED="false"
EVIDENCE_FILE="${OUT}/.evidence.txt"
: > "${EVIDENCE_FILE}"
add_ev() { echo "$1" >> "${EVIDENCE_FILE}"; }

# P1 — settings.json (sélection persistée)
if [ -f "${CFG}/settings.json" ]; then
  cp "${CFG}/settings.json" "${OUT}/settings.json" 2>/dev/null
  SEL="$(jq -r '.freebuffModel // empty' "${CFG}/settings.json" 2>/dev/null)"
  [ -n "${SEL}" ] && add_ev "settings.freebuffModel=${SEL}"
fi

# P2 — log.jsonl du dernier chat ayant exécuté des steps agent
AGENT_LINES=""
LATEST=""
for d in "${CFG}"/projects/*/chats/*/; do
  f="${d}log.jsonl"
  [ -f "${f}" ] || continue
  if grep -q "Start agent" "${f}" 2>/dev/null; then
    LATEST="${f}"
  fi
done
if [ -n "${LATEST}" ]; then
  cp "${LATEST}" "${OUT}/chat-log.jsonl" 2>/dev/null
  AGENT_LINES="$(grep -oE '"msg":"(Start|End) agent [^"]+' "${LATEST}" | head -20)"
  add_ev "chat_log=$(basename "$(dirname "${LATEST}")")"
fi
if [ -n "${AGENT_LINES}" ]; then
  echo "${AGENT_LINES}" > "${OUT}/agent-lines.txt"
  if echo "${AGENT_LINES}" | grep -qE "agent [a-z0-9-]*${EXPECTED_AGENT}[a-z0-9-]*"; then
    OBSERVED="${REQ}"
    if [ "${SEL:-}" = "${REQ}" ]; then
      STATE="MUSE_OK"
      CERTIFIED="true"
      add_ev "agent_log=base3-free-muse-spark-1-3 (provisionné serveur)"
    else
      STATE="MUSE_OK_AGENT_ONLY"   # agent muse mais settings divergents
    fi
  else
    OTHER="$(echo "${AGENT_LINES}" | grep -oE 'agent [a-z0-9-]+' | head -1)"
    STATE="MUSE_FALLBACK"
    add_ev "agent_log=${OTHER:-autre modèle} (fallback présumé)"
  fi
fi

# P3 — transcript : mention du modèle cible dans le header
if ls /reviewer/out/transcript-*.txt >/dev/null 2>&1; then
  if grep -qE "Muse Spark.*·" /reviewer/out/transcript-*.txt 2>/dev/null; then
    add_ev "transcript.header=Muse Spark"
  fi
fi

# Version CLI (binaire natif)
if [ -x "${CFG}/freebuff" ]; then
  "${CFG}/freebuff" --version > "${OUT}/cli-version.txt" 2>&1 || true
fi

EV_JSON="$(jq -R -s 'split("\n")[:-1]' < "${EVIDENCE_FILE}" 2>/dev/null || echo '[]')"
rm -f "${EVIDENCE_FILE}"
OBS_JSON="${OBSERVED}"
[ -z "${OBS_JSON}" ] && OBS_JSON="null"

# Construction JSON robuste via jq (jamais de concaténation manuelle non quotée).
jq -n \
  --arg req "${REQ}" \
  --arg obs "${OBS_JSON}" \
  --arg state "${STATE}" \
  --argjson certified "${CERTIFIED}" \
  --argjson ev "${EV_JSON}" \
  '{model_requested:$req, model_observed:($obs | if . == "null" then null else . end), state:$state, certified:$certified, evidence:$ev}' \
  > "${OUT}/model.json"
cat "${OUT}/model.json" > /dev/null  # jq écrit déjà le fichier
echo "probe -> ${OUT}/model.json"
