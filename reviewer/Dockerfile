# Reviewer Freebuff isolé — image minimale ARM64/x64.
# But : exécuter Freebuff CLI (TUI) sous tmux dans une sandbox durcie, sans MCP,
# sans sous-agent, code à reviewer monté read-only.
#
# Version épinglée : freebuff@0.0.171 (le launcher npm télécharge le binaire natif
# correspondant dans ~/.config/manicode au premier lancement).
# NOTE reproductibilité : le binaire natif est figé par la version npm ; le launcher
# peut proposer une màj au démarrage — la version réellement servie est journalisée
# à chaque review (voir orchestrator/model_probe.sh).

FROM node:22-bookworm-slim AS base

# tmux + utilitaires minimaux. PAS de docker CLI, PAS de ssh client, PAS de gh,
# PAS de compilateurs, PAS de MCP.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tmux ca-certificates curl jq \
 && rm -rf /var/lib/apt/lists/*

# Utilisateur non-root dédié (pas de collision UID avec l'hôte).
ARG REVIEWER_UID=3741
ARG REVIEWER_GID=3741
RUN groupadd -g ${REVIEWER_GID} reviewer \
 && useradd -m -u ${REVIEWER_UID} -g reviewer -s /bin/bash reviewer

# Freebuff CLI épinglé (launcher npm).
RUN npm install --global --no-audit --no-fund --loglevel=error freebuff@0.0.171

# Structure du reviewer (instructions/skills TRUSTED, hors repo analysé).
RUN mkdir -p /reviewer/.agents/skills/pr-review \
             /reviewer/orchestrator \
             /reviewer/out \
             /reviewer/pr \
 && chown -R reviewer:reviewer /reviewer

COPY --chown=reviewer:reviewer AGENTS.md /reviewer/AGENTS.md
COPY --chown=reviewer:reviewer .agents/skills/pr-review/SKILL.md \
     /reviewer/.agents/skills/pr-review/SKILL.md

# Scripts d'automatisation (exécutés DANS le conteneur).
COPY --chown=reviewer:reviewer orchestrator/tui_driver.sh \
     orchestrator/model_probe.sh /reviewer/orchestrator/
RUN chmod +x /reviewer/orchestrator/*.sh

# Pré-créer les répertoires d'état Freebuff (sinon le volume nommé est créé
# root-owned au premier montage).
RUN mkdir -p /home/reviewer/.config/manicode /home/reviewer/.freebuff \
 && chown -R reviewer:reviewer /home/reviewer

USER reviewer
WORKDIR /reviewer

ENV HOME=/home/reviewer
ENV NO_COLOR=1

# tmux : TERM requis. Le TUI Freebuff gère mal les TERM exotiques.
ENV TERM=xterm-256color

CMD ["/bin/bash", "-lc", "exec tmux new-session -d -s freebuff 'freebuff' && exec tmux attach -t freebuff"]

# Usage production (orchestrateur, hôte) :
#   docker run --rm --name fb-review-<job> --init \
#     --read-only \
#     --user 3741:3741 --security-opt no-new-privileges:true \
#     --cap-drop ALL --pids-limit 128 --memory 1536m --cpus 2.0 \  #     --tmpfs /tmp:rw,size=256m,mode=1777,exec \   # exec requis (OpenTUI dlopen depuis tmpdir)    # -v freebuff-reviewer-auth:/home/reviewer/.config/manicode \
    # -v freebuff-reviewer-state:/home/reviewer/.freebuff \
    # -v <snapshot_input>:/reviewer/pr:ro \
    # -v <output_dir>:/reviewer/out \
#     freebuff-reviewer:0.1.0 \
#     bash /reviewer/orchestrator/tui_driver.sh --job <job>
