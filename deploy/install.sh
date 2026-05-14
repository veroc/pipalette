#!/usr/bin/env bash
# piPalette installer for Raspberry Pi OS Lite (Bookworm).
#
# Usage (fresh Pi):
#   curl -sSL https://raw.githubusercontent.com/veroc/pipalette/master/deploy/install.sh | sudo bash
#
# Flags (after `bash` add `-s -- <flag>...`):
#   --no-lean      skip the lean-Pi tweaks (Bluetooth/audio/swap/etc.)
#   --channel=TAG  pin to a specific tag instead of the latest release
#   --ref=REF      use a non-default git ref (branch/commit) — escape hatch
#   --prefix=DIR   install prefix (default: /opt/pipalette)
#
# Idempotent: safe to re-run. Existing checkout is reused; venv is reused.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/veroc/pipalette.git}"
PREFIX="/opt/pipalette"
STATE_DIR="/var/lib/pipalette"
APPLY_LEAN=1
CHANNEL=""
REF=""
# scsi2pi version we install if not already present. Bump in lockstep with
# what pp8k has been validated against (see project memory).
SCSI2PI_VERSION="6.2.1"

for arg in "$@"; do
  case "$arg" in
    --no-lean) APPLY_LEAN=0 ;;
    --channel=*) CHANNEL="${arg#*=}" ;;
    --ref=*) REF="${arg#*=}" ;;
    --prefix=*) PREFIX="${arg#*=}" ;;
    --repo-url=*) REPO_URL="${arg#*=}" ;;
    --scsi2pi-version=*) SCSI2PI_VERSION="${arg#*=}" ;;
    --no-scsi2pi) SCSI2PI_VERSION="" ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Error: this installer needs to run as root (try: sudo bash)" >&2
  exit 1
fi

# Resolve the human user that invoked sudo so the service runs as them, not root.
RUN_USER="${SUDO_USER:-}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
  echo "Error: please run this installer via sudo from your normal user account." >&2
  echo "       The piPalette service should not run as root." >&2
  exit 1
fi
RUN_GROUP="$(id -gn "$RUN_USER")"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"

log() { printf '\033[36m[install]\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m[ ok ]\033[0m %s\n' "$*"; }

# -- apt deps --------------------------------------------------------------

log "Installing apt packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  git \
  python3 \
  python3-venv \
  python3-pip \
  avahi-daemon \
  libcap2-bin \
  ca-certificates \
  curl

ok "apt packages installed"

# -- scsi2pi (PiSCSI HAT driver) ------------------------------------------

if [[ -n "$SCSI2PI_VERSION" ]]; then
  if [[ -x /opt/scsi2pi/bin/s2pexec ]] && \
     /opt/scsi2pi/bin/s2pexec --version 2>&1 | grep -qF "$SCSI2PI_VERSION"; then
    ok "scsi2pi $SCSI2PI_VERSION already installed"
  else
    DISTRO_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    ARCH="$(dpkg --print-architecture)"
    DEB_NAME="scsi2pi_${SCSI2PI_VERSION}_${DISTRO_CODENAME}_${ARCH}-1.deb"
    DEB_URL="https://www.scsi2pi.net/packages/releases/${DEB_NAME}"
    DEB_PATH="/tmp/${DEB_NAME}"
    log "Downloading scsi2pi ${SCSI2PI_VERSION} for ${DISTRO_CODENAME}/${ARCH}…"
    if ! curl -fsSL -o "$DEB_PATH" "$DEB_URL"; then
      echo "Error: failed to download $DEB_URL" >&2
      echo "       Pick a known version with --scsi2pi-version=X.Y, or skip with --no-scsi2pi." >&2
      exit 1
    fi
    log "Installing scsi2pi…"
    apt-get install -y -qq "$DEB_PATH"
    rm -f "$DEB_PATH"
    ok "scsi2pi $SCSI2PI_VERSION installed"
  fi
fi

# -- clone / update repo ---------------------------------------------------

if [[ -d "$PREFIX/.git" ]]; then
  log "Updating existing checkout at $PREFIX…"
  git -C "$PREFIX" fetch --tags --prune
else
  log "Cloning $REPO_URL → $PREFIX…"
  mkdir -p "$(dirname "$PREFIX")"
  git clone --quiet "$REPO_URL" "$PREFIX"
  git -C "$PREFIX" fetch --tags --prune
fi

# Pick the ref to check out. With no flag and no tags yet, leave the
# clone on its default branch (works for both `main` and `master`).
if [[ -n "$REF" ]]; then
  TARGET="$REF"
elif [[ -n "$CHANNEL" ]]; then
  TARGET="$CHANNEL"
else
  TARGET="$(git -C "$PREFIX" tag --sort=-v:refname | head -1)"
fi

if [[ -n "$TARGET" ]]; then
  log "Checking out $TARGET…"
  git -C "$PREFIX" checkout --quiet "$TARGET"
else
  CURRENT_BRANCH="$(git -C "$PREFIX" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
  # On re-runs the fetch above only updates remote-tracking refs. Fast-forward
  # the current branch to its upstream so the working tree matches what we
  # just fetched.
  if git -C "$PREFIX" rev-parse --abbrev-ref @{u} >/dev/null 2>&1; then
    log "No tag/ref specified — fast-forwarding $CURRENT_BRANCH to upstream"
    if ! git -C "$PREFIX" merge --ff-only --quiet @{u}; then
      echo "Error: $CURRENT_BRANCH has diverged from upstream; can't fast-forward." >&2
      echo "       Reset the checkout at $PREFIX or pass --ref= explicitly." >&2
      exit 1
    fi
  else
    log "No tag/ref specified — staying on $CURRENT_BRANCH (no upstream)"
  fi
  TARGET="$CURRENT_BRANCH"
fi

chown -R "$RUN_USER:$RUN_GROUP" "$PREFIX"

# -- venv + pip install ----------------------------------------------------

log "Setting up Python virtualenv…"
if [[ ! -d "$PREFIX/.venv" ]]; then
  sudo -u "$RUN_USER" python3 -m venv "$PREFIX/.venv"
fi
sudo -u "$RUN_USER" "$PREFIX/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$PREFIX/.venv/bin/pip" install --quiet -e "$PREFIX"
# waitress is the production WSGI server — pure Python, no native compile.
# Flask's dev server is fine for development but we want a proper runner.
sudo -u "$RUN_USER" "$PREFIX/.venv/bin/pip" install --quiet "waitress>=3.0"

ok "virtualenv ready"

# -- state + data dirs ----------------------------------------------------

mkdir -p "$STATE_DIR"
chown "$RUN_USER:$RUN_GROUP" "$STATE_DIR"
chmod 0755 "$STATE_DIR"

# The app expects $PREFIX/data to be writable. ReadWritePaths= requires the
# path to exist at unit-start time, so make sure it's there.
mkdir -p "$PREFIX/data"
chown -R "$RUN_USER:$RUN_GROUP" "$PREFIX/data"

# -- systemd unit ----------------------------------------------------------

log "Installing systemd units…"
SERVICE_PATH="/etc/systemd/system/pipalette.service"
UPDATE_PATH="/etc/systemd/system/pipalette-update.service"

# Render templates with the resolved user / paths.
sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@GROUP@|$RUN_GROUP|g" \
    -e "s|@PREFIX@|$PREFIX|g" \
    -e "s|@STATE_DIR@|$STATE_DIR|g" \
    "$PREFIX/deploy/pipalette.service" > "$SERVICE_PATH"

sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@GROUP@|$RUN_GROUP|g" \
    -e "s|@PREFIX@|$PREFIX|g" \
    -e "s|@STATE_DIR@|$STATE_DIR|g" \
    "$PREFIX/deploy/pipalette-update.service" > "$UPDATE_PATH"

chmod 0644 "$SERVICE_PATH" "$UPDATE_PATH"

# systemd's AmbientCapabilities handles port-80 binding for us — no setcap
# on the python binary needed, which keeps the venv portable across reinstalls.

# -- sudoers ---------------------------------------------------------------

SUDOERS_PATH="/etc/sudoers.d/pipalette"
sed -e "s|@USER@|$RUN_USER|g" "$PREFIX/deploy/sudoers.pipalette" > "$SUDOERS_PATH"
chmod 0440 "$SUDOERS_PATH"
visudo -cf "$SUDOERS_PATH" >/dev/null

# -- Avahi (Bonjour) advertisement ----------------------------------------

mkdir -p /etc/avahi/services
cp "$PREFIX/deploy/pipalette.avahi.service" /etc/avahi/services/pipalette.service
chmod 0644 /etc/avahi/services/pipalette.service

# -- hostname (only if still on the default 'raspberrypi') ----------------

CURRENT_HOSTNAME="$(hostname)"
if [[ "$CURRENT_HOSTNAME" == "raspberrypi" ]]; then
  log "Renaming host raspberrypi → pipalette (for pipalette.local mDNS)…"
  hostnamectl set-hostname pipalette
  sed -i "s/127\.0\.1\.1\s\+raspberrypi/127.0.1.1\tpipalette/" /etc/hosts || true
fi

# -- lean Pi profile (optional) -------------------------------------------

if [[ $APPLY_LEAN -eq 1 ]]; then
  log "Applying lean-Pi profile (Bluetooth/audio/swap/etc.)…"
  bash "$PREFIX/deploy/lean-pi.sh"
fi

# -- enable + start --------------------------------------------------------

systemctl daemon-reload
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
systemctl enable pipalette.service >/dev/null 2>&1 || true
# `restart` (not `start`) so unit-file edits on a re-run actually take effect.
systemctl restart pipalette.service
# Bounce avahi too — hostname may have just changed; this re-advertises.
systemctl restart avahi-daemon >/dev/null 2>&1 || true

ok "pipalette.service started"

echo
echo "Done."
echo "Visit http://pipalette.local/  (or http://$(hostname -I | awk '{print $1}')/)"
echo "Logs:  sudo journalctl -u pipalette -f"
echo "Version installed: $TARGET"
