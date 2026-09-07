#!/usr/bin/env bash
# tui_driver.sh — Automatisation tmux du TUI Freebuff (exécuté DANS le conteneur).
# Machine d'état bornée (aucune boucle infinie). Logs stderr, transcript fichier.
#
# Calibration UI (freebuff v0.0.171, vérifiée 2026-09-06) :
#   - écran login  : "Press ENTER to login..."
#   - onboarding   : popups "Meet Freebucks" / "Press any key to continue"
#   - sélecteur    : "/model" Enter → landing "Start coding for free" (curseur = modèle courant)
#                    Down Enter → liste des 6 modèles (curseur ›)
#                    ordre : Solar Pro 4, GLM 5.3 Flash, MiMo 2.5, DeepSeek V4 Flash,
#                            Muse Spark 1.3, GPT-5.6 Luna
#   - chat         : "Enter a coding task or / for commands" ; modèle en cours dans le header
#
# Usage: tui_driver.sh [--job <id>] [--prompt-file <path>] [--timeout-s <s>] [--model <name>]
# Env: FAKE_TUI=1 (tests hors quota), FB_MODEL_TARGET, FB_DEBUG=1
set -u

JOB="review-$$"
PROMPT_FILE=""
TIMEOUT_S=900
STEP_TIMEOUT_S=90
FAKE_TUI="${FAKE_TUI:-0}"
FB_MODEL_TARGET="${FB_MODEL_TARGET:-meta/muse-spark-1.3-contributor}"
# Nom affiché dans la liste (pour la navigation) — résolu depuis FB_MODEL_TARGET si connu.
MODEL_LIST_NAME="Muse Spark 1.3"
MODEL_ROWS=("Solar Pro 4" "GLM 5.3 Flash" "MiMo 2.5" "DeepSeek V4 Flash" "Muse Spark 1.3" "GPT-5.6 Luna")
FB_REVIEW_KEYS="Enter"
OUT_DIR="${OUT_DIR:-/reviewer/out}"
DEBUG="${FB_DEBUG:-0}"

while [ $# -gt 0 ]; do
  case "$1" in
    --job) JOB="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
    --timeout-s) TIMEOUT_S="$2"; shift 2 ;;
    --model) FB_MODEL_TARGET="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

mkdir -p "${OUT_DIR}"
# Nettoyage du volume de sortie partagé : jamais de résultat stale d'un job précédent.
rm -f "${OUT_DIR}/result.json" "${OUT_DIR}/state-"*.json "${OUT_DIR}/transcript-"*.txt 2>/dev/null || true
rm -rf "${OUT_DIR}/evidence-"* 2>/dev/null || true
LOG="${OUT_DIR}/driver-${JOB}.log"
TRANSCRIPT="${OUT_DIR}/transcript-${JOB}.txt"
EVIDENCE="${OUT_DIR}/evidence-${JOB}"
mkdir -p "${EVIDENCE}"

say()  { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG}" >&2; }
dbg()  { [ "${DEBUG}" = "1" ] && say "DBG: $*"; return 0; }
die()  { say "FATAL: $*"; cleanup; exit 1; }

cleanup() { tmux kill-session -t "${SESSION}" 2>/dev/null; return 0; }
trap cleanup EXIT

tsend() { tmux send-keys -t "${SESSION}" -l "$1"; }
tkey()  { tmux send-keys -t "${SESSION}" "$1"; }
tpane() { tmux capture-pane -t "${SESSION}" -p 2>/dev/null || true; }

twait() { # twait <label> <regex> <timeout_s> [file]
  local label="$1" regex="$2" tmo="$3" i=0
  while [ "$i" -lt "$tmo" ]; do
    if tpane | grep -Eq "$regex"; then say "OK wait:${label} (t=${i}s)"; return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

row_index_of_current() { # lit la ligne avec › et renvoie l'index du modèle dans MODEL_ROWS
  local line; line="$(tpane | grep -E "›" | head -1)"
  local i=0 name
  for name in "${MODEL_ROWS[@]}"; do
    if echo "$line" | grep -q "$name"; then echo "$i"; return 0; fi
    i=$((i+1))
  done
  echo "-1"
}

# --- étape 1 : démarrage ------------------------------------------------------
SESSION="fb-${JOB}"
if [ "${FAKE_TUI}" = "1" ]; then
  mkdir -p /tmp/fake-tui
  cat > /tmp/fake-tui/fake.sh <<'FAKEEOF'
#!/usr/bin/env bash
sleep 1; printf 'Freebuff ready (fake). Type /model or ask me.\n'
sleep 6
printf '\n>>> fake model menu: meta/muse-spark-1.3-contributor (selected)\n'
sleep 2
printf '\nreviewing...\n'
sleep 2
printf '\n<<<PR_REVIEW_RESULT_V1>>>\n{"schema_version":1,"status":"PASS","findings":[]}\n<<<END_PR_REVIEW_RESULT_V1>>>\n'
sleep 600
FAKEEOF
  chmod +x /tmp/fake-tui/fake.sh
  CMD="bash /tmp/fake-tui/fake.sh"
else
  CMD="freebuff"
fi

# --- étape 0 : pré-sélection du modèle (mode réel) -----------------------------
# Écriture de settings.json AVANT le lancement : le sélecteur démarre sur le modèle
# cible → Enter = session Muse Spark (déterministe, sans navigation fragile).
if [ "${FAKE_TUI}" != "1" ]; then
  CFG_FILE="${HOME}/.config/manicode/settings.json"
  if [ -f "${CFG_FILE}" ]; then
    CUR="$(jq -r '.freebuffModel // empty' "${CFG_FILE}" 2>/dev/null || true)"
    if [ "${CUR}" != "${FB_MODEL_TARGET}" ]; then
      TMPF="${CFG_FILE}.tmp"
      jq --arg m "${FB_MODEL_TARGET}" '.freebuffModel = $m' "${CFG_FILE}" > "${TMPF}" 2>/dev/null \
        && mv "${TMPF}" "${CFG_FILE}" \
        && say "preselect model: settings.freebuffModel=${FB_MODEL_TARGET}"
    else
      say "preselect OK (déjà ${FB_MODEL_TARGET})"
    fi
  fi
fi

say "start session ${SESSION} (job=${JOB}, fake=${FAKE_TUI})"
tmux new-session -d -s "${SESSION}" -x 220 -y 55 "${CMD}" 2>>"${LOG}" || die "tmux new-session"
tmux set-option -t "${SESSION}" allow-passthrough off 2>/dev/null
tmux set-option -t "${SESSION}" history-limit 50000 2>/dev/null

# --- étape 2 : attente prêt / login / takeover ----------------------------------
# Contrainte live : UNE instance freebuff par compte. Si le Desktop (ou un autre
# CLI) tourne, l'écran « Freebuff is already running » propose [Take over][Exit].
# Politique PRODUCTION : NE JAMAIS cliquer « Take over » automatiquement →
# INSTANCE_BUSY + fail-closed. FB_ALLOW_TAKEOVER=1 n'est autorisé que pour les
# tests E2E explicitement consentis (Titou sait qu'un test tourne).
ALLOW_TAKEOVER="${FB_ALLOW_TAKEOVER:-0}"
ready_screen() { tpane | grep -qE "Enter a coding task|Start coding for free|Press ENTER to login|Freebuff ready"; }

TAKEOVER_SEEN=0
i=0; READY=0
while [ "$i" -lt "${STEP_TIMEOUT_S}" ]; do
  if ready_screen; then READY=1; say "OK wait:ready (t=${i}s)"; break; fi
  if tpane | grep -q "already running"; then
    if [ "${TAKEOVER_SEEN}" = "0" ]; then
      TAKEOVER_SEEN=1
      tpane > "${EVIDENCE}/03-takeover.txt"
      if [ "${ALLOW_TAKEOVER}" = "1" ]; then
        say "instance déjà active — Take over autorisé pour ce test (Enter)"
        tkey Enter; sleep 3
      else
        say "INSTANCE_BUSY : une autre instance Freebuff est active (Desktop utilisateur ?)"
        echo '{"state":"INSTANCE_BUSY"}' > "${OUT_DIR}/state-${JOB}.json"
        exit 30
      fi
    fi
  fi
  sleep 1; i=$((i+1))
done
if [ "${READY}" != "1" ]; then
  tpane > "${EVIDENCE}/01-not-ready.txt"; die "TIMEOUT waiting for readiness"
fi
tpane > "${EVIDENCE}/02-initial.txt"

if tpane | grep -q "Press ENTER to login"; then
  say "auth required"
  echo '{"state":"AUTH_REQUIRED"}' > "${OUT_DIR}/state-${JOB}.json"
  exit 10
fi

# Onboarding "any key" répété si présent.
for _ in 1 2 3; do
  if tpane | grep -qE "Press any key to continue"; then tkey Enter; sleep 2; fi
done

# --- étape 3 : (mode réel) sélection du modèle ---------------------------------
if [ "${FAKE_TUI}" != "1" ]; then
  # 3a. Réutilisation : si une session Muse Spark est DÉJÀ active dans le chat,
  #     on ne redémarre pas de session (pas de double débit Freebucks).
  if tpane | grep -q "Enter a coding task" && tpane | grep -qE "Muse Spark[^·]*·[0-9]|Muse Spark[^·]*·"; then
    say "OK session Muse déjà active — réutilisation"
  else
    # 3b. ouvrir le sélecteur depuis le chat (si on y est)
    if tpane | grep -q "Enter a coding task"; then
      say "open model selector (/model)"
      tsend "/model"; tkey Enter; sleep 3
    fi
    # 3c. landing : la pré-sélection (étape 0) place Muse en tête → Enter suffit.
    #     Vérifier d'abord que la carte mise en avant contient bien Muse Spark.
    if tpane | grep -qE "Start coding for free"; then
      if tpane | grep -qE "›.*Muse Spark|Muse Spark[^·]*·[0-9]+ Freebucks"; then
        say "landing: Muse Spark pré-sélectionné — Enter direct"
      else
        say "landing: carte ≠ Muse (état inattendu) — expansion + recherche"
        tkey Down; tkey Enter; sleep 3
        tpane > "${EVIDENCE}/03-model-list.txt"
        n=0; FOCUSED=0
        while [ "$n" -lt 6 ]; do
          if tpane | grep -qE "›.*Muse Spark"; then FOCUSED=1; break; fi
          tkey Down; sleep 1; n=$((n+1))
        done
        tpane > "${EVIDENCE}/04-model-target.txt"
        [ "${FOCUSED}" = "1" ] && say "OK Muse Spark focused (${n} downs)" \
                                 || say "WARN: Muse Spark pas trouvé"
      fi
    fi
    # 3d. démarrer la session (débit Freebucks : ~15/h)
    tkey Enter; sleep 6
    tpane > "${EVIDENCE}/05-after-model-select.txt"
    if tpane | grep -qE "Enter a coding task|Start coding for free"; then
      say "OK session started (chat ou landing)"
    else
      tpane > "${EVIDENCE}/05b-unexpected.txt"; say "WARN: état inattendu après sélection"
    fi
    # 3f. modèle affiché dans le header (témoin certification)
    if tpane | grep -qE "Muse Spark.*·"; then
      say "OK header session = Muse Spark"
    else
      say "WARN: header ne montre pas Muse Spark"
    fi
    # 3g. onboarding éventuel
    for _ in 1 2 3; do
      if tpane | grep -qE "Press any key to continue"; then tkey Enter; sleep 2; fi
    done
  fi
fi

# --- étape 4 : injection du prompt ---------------------------------------------
if [ -n "${PROMPT_FILE}" ] && [ -f "${PROMPT_FILE}" ]; then
  PROMPT_CONTENT="$(cat "${PROMPT_FILE}")"
else
  PROMPT_CONTENT="Review the pull request whose snapshot is in /reviewer/pr/repository (read context in /reviewer/pr/context.json). Apply the pr-review skill and return your verdict exactly in the required JSON format."
fi
say "inject prompt (${#PROMPT_CONTENT} chars)"
tsend "${PROMPT_CONTENT}"
tkey "${FB_REVIEW_KEYS}"
say "prompt sent"

# --- étape 5 : attente du résultat ---------------------------------------------
say "wait for result (timeout ${TIMEOUT_S}s)"
i=0
FOUND=""
while [ "$i" -lt "${TIMEOUT_S}" ]; do
  if [ -f "${OUT_DIR}/result.json" ] && grep -q "schema_version" "${OUT_DIR}/result.json" 2>/dev/null; then
    FOUND="result.json"; say "OK result.json present"; break
  fi
  if tpane | grep -q "END_PR_REVIEW_RESULT_V1"; then
    FOUND="sentinel"; say "OK sentinel found in pane"; break
  fi
  sleep 2; i=$((i+2))
done

# --- étape 6 : captures finales -------------------------------------------------
tmux capture-pane -t "${SESSION}" -p -S -20000 > "${TRANSCRIPT}" 2>/dev/null
tpane > "${EVIDENCE}/06-final.txt"
# Header (modèle affiché en session) : capture témoin.
tmux capture-pane -t "${SESSION}" -p -S -5 > "${EVIDENCE}/07-header.txt" 2>/dev/null

if [ "${FOUND}" = "result.json" ]; then exit 0; fi
if [ "${FOUND}" = "sentinel" ]; then exit 3; fi
say "NO RESULT (timeout)"
echo '{"state":"TIMEOUT_OR_NO_RESULT"}' > "${OUT_DIR}/state-${JOB}.json"
exit 20
