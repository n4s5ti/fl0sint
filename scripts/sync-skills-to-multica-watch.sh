#!/usr/bin/env bash
###############################################################################
# Watch OMP skill directories for changes and auto-sync to multica.
#
# Uses the vendored chokidar-cli from pip3r to watch skill file changes
# and trigger the sync script with a debounce.
#
# Supports symlinked skills (OMP -> Hermes global): passes --follow-symlinks
# so changes to the real source files through the symlink are detected.
#
# Usage:
#
# Systemd (permanent, auto-start on login):
#   systemctl --user daemon-reload
#   systemctl --user enable --now multica-skill-sync
# Management:
#   systemctl --user status multica-skill-sync
#   systemctl --user stop multica-skill-sync
#   systemctl --user start multica-skill-sync
#   systemctl --user restart multica-skill-sync
#   journalctl --user -u multica-skill-sync -f
#
INSTALL
#   ./scripts/sync-skills-to-multica-watch.sh          # foreground
#   nohup ./scripts/sync-skills-to-multica-watch.sh &   # background
###############################################################################

set -euo pipefail
export SHELL="${SHELL:-/usr/bin/bash}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync-skills-to-multica.py"
CHOKIDAR="/home/n4s5ti/Documents/dev/pip3r/packages/chokidar-cli/vendor/chokidar-cli/index.js"

SKILL_DIRS=(
  "$HOME/.omp/agent/skills"
  "$HOME/.omp/agent/managed-skills"
)

# Convert to chokidar patterns: watch all files recursively
PATTERNS=()
for dir in "${SKILL_DIRS[@]}"; do
  if [[ -d "$dir" ]]; then
    PATTERNS+=("$dir/**/*")
  fi
done

if [[ ${#PATTERNS[@]} -eq 0 ]]; then
  echo "Error: no skill directories found" >&2
  exit 1
fi

echo "=== Skill Sync Watcher ==="
echo "Watching:"
for dir in "${SKILL_DIRS[@]}"; do
  echo "  $dir"
done
echo "Follow symlinks: yes"
echo "Debounce: 2s"
echo "On change: $SYNC_SCRIPT"
echo "=========================="
echo

exec node "$CHOKIDAR" "${PATTERNS[@]}" \
  --follow-symlinks \
  --debounce 2000 \
  --initial \
  --command "python3 \"$SYNC_SCRIPT\" --quiet 2>&1" \
  --verbose
