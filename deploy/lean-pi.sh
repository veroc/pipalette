#!/usr/bin/env bash
# Lean-Pi profile: disable hardware/services the piPalette unit doesn't use.
# Reversible — saves the previous /boot/firmware/config.txt to a .bak file.
#
# What it does:
#   - Bluetooth (dtoverlay=disable-bt) — saves power, reduces 2.4 GHz noise
#   - Audio (dtparam=audio=off)
#   - HDMI output powered down at boot
#   - Swap (dphys-swapfile) — saves SD writes
#   - Background services we don't need (unattended-upgrades, triggerhappy,
#     ModemManager, cups) — frees RAM/CPU, avoids surprises during exposure
#
# Safe to re-run.

set -euo pipefail

CONFIG="/boot/firmware/config.txt"
if [[ ! -f "$CONFIG" ]]; then
  CONFIG="/boot/config.txt"  # pre-Bookworm path
fi

log() { printf '[lean-pi] %s\n' "$*"; }

if [[ ! -f "$CONFIG" ]]; then
  log "config.txt not found at /boot/firmware/config.txt or /boot/config.txt — skipping boot tweaks"
else
  if [[ ! -f "${CONFIG}.pipalette.bak" ]]; then
    cp "$CONFIG" "${CONFIG}.pipalette.bak"
    log "backed up $CONFIG → ${CONFIG}.pipalette.bak"
  fi

  # Idempotently ensure each tweak appears exactly once.
  add_once() {
    local line="$1"
    grep -qxF "$line" "$CONFIG" || printf '%s\n' "$line" >> "$CONFIG"
  }
  if ! grep -q "^# --- piPalette lean profile ---$" "$CONFIG"; then
    printf '\n# --- piPalette lean profile ---\n' >> "$CONFIG"
  fi
  add_once "dtoverlay=disable-bt"
  add_once "dtparam=audio=off"
  log "boot config: Bluetooth + audio disabled (reboot to take effect)"
fi

# Power down HDMI at boot via a systemd one-shot. vcgencmd is on $PATH on Pi OS.
HDMI_UNIT=/etc/systemd/system/pipalette-hdmi-off.service
if [[ ! -f $HDMI_UNIT ]]; then
  cat >"$HDMI_UNIT" <<'EOF'
[Unit]
Description=Power down HDMI output (piPalette lean profile)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/vcgencmd display_power 0
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl enable --quiet pipalette-hdmi-off.service || true
  log "HDMI power-down service installed"
fi

# Swap off (dphys-swapfile is the Pi OS default swap manager).
if systemctl list-unit-files | grep -q '^dphys-swapfile\.service'; then
  systemctl disable --now dphys-swapfile.service >/dev/null 2>&1 || true
  log "swap disabled (dphys-swapfile)"
fi

# Background services we don't want fighting for CPU.
for svc in bluetooth.service hciuart.service \
           unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer \
           triggerhappy.service ModemManager.service cups.service cups.socket \
           cups-browsed.service; do
  if systemctl list-unit-files | grep -q "^${svc}"; then
    systemctl disable --now "$svc" >/dev/null 2>&1 || true
  fi
done
log "background services disabled (bluetooth, apt timers, triggerhappy, ModemManager, cups)"

# Mask the units so they don't get re-enabled by package upgrades.
for svc in bluetooth.service hciuart.service unattended-upgrades.service; do
  systemctl mask "$svc" >/dev/null 2>&1 || true
done

log "done. Reboot to apply boot-level changes."
