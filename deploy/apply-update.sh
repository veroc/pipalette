#!/usr/bin/env bash
# Runs (as root, via pipalette-update.service) when the user clicks
# "Update now" in the app. Reads the target git ref from the pending-update
# state file, fetches, checks out, reinstalls deps, restarts the service.

set -euo pipefail

PREFIX="${PIPALETTE_PREFIX:-/opt/pipalette}"
RUN_USER="${PIPALETTE_USER:-pi}"
STATE_DIR="${PIPALETTE_STATE_DIR:-/var/lib/pipalette}"
PENDING_FILE="$STATE_DIR/pending-update"
STATUS_FILE="$STATE_DIR/update-status"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

write_status() {
  printf '%s\n' "$1" > "$STATUS_FILE.tmp"
  mv "$STATUS_FILE.tmp" "$STATUS_FILE"
  chown "$RUN_USER:" "$STATUS_FILE" 2>/dev/null || true
}

if [[ ! -f "$PENDING_FILE" ]]; then
  log "no pending update; nothing to do"
  write_status "idle"
  exit 0
fi

TARGET="$(< "$PENDING_FILE")"
TARGET="${TARGET//[$'\t\r\n ']/}"
if [[ -z "$TARGET" ]]; then
  log "pending-update file empty"
  write_status "error: empty target"
  exit 1
fi

log "updating to $TARGET"
write_status "fetching"

# Fetch as the run user so the working tree stays owned consistently.
sudo -u "$RUN_USER" git -C "$PREFIX" fetch --tags --prune

write_status "checking out $TARGET"
sudo -u "$RUN_USER" git -C "$PREFIX" checkout --quiet "$TARGET"

write_status "installing deps"
# `pip install -e .` re-resolves pp8k from pyproject.toml — picks up any
# tag bump that came with the new release.
sudo -u "$RUN_USER" "$PREFIX/.venv/bin/pip" install --quiet -e "$PREFIX"

write_status "restarting"
# Restart in the background so this oneshot unit can exit cleanly while
# pipalette.service comes back up.
systemctl restart pipalette.service

rm -f "$PENDING_FILE"
write_status "ok: $TARGET"
log "update to $TARGET complete"
