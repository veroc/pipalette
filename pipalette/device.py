"""Thin wrapper around pp8k -- open/mock, status caching, discovery."""

import glob
import shutil
import subprocess
import threading
import time

import pp8k


# How long a cached snapshot is considered fresh before we re-query the device.
SNAPSHOT_TTL_SECONDS = 2.0


class DeviceManager:
    """Owns the lifecycle of the active pp8k.Device.

    Re-opens the underlying transport on demand if the config target changes.
    Caches the slot/info/mode snapshot briefly so the UI can poll cheaply.
    """

    def __init__(self, config):
        self._config = config
        self._lock = threading.Lock()
        self._device = None
        self._opened_mock = None
        self._opened_target = None
        self._snapshot = None
        self._snapshot_time = 0.0
        self._last_error = None

    def _needs_reopen(self):
        return (
            self._device is None
            or bool(self._config.get("mock_mode")) != bool(self._opened_mock)
            or self._config.get("target") != self._opened_target
        )

    def _open(self):
        mock_mode = bool(self._config.get("mock_mode"))
        target = self._config.get("target")
        if mock_mode:
            device = pp8k.mock()
        else:
            if target in (None, ""):
                raise ValueError(
                    "No hardware target configured — press Scan to discover one, "
                    "or switch to Mock mode."
                )
            # pp8k.open() dispatches: int / digit-string → s2pexec, path → sgio.
            device = pp8k.open(int(target) if str(target).isdigit() else str(target))
        self._device = device
        self._opened_mock = mock_mode
        self._opened_target = target
        self._snapshot = None
        self._snapshot_time = 0.0
        self._last_error = None

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._opened_mock = None
        self._opened_target = None
        self._snapshot = None

    def reopen(self):
        with self._lock:
            self._close_locked()
            try:
                self._open()
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def status(self, force=False):
        """Return a UI-facing snapshot of device state.

        Always returns a dict, even when the device is unreachable -- in
        that case `connected` is False and `error` carries the reason.
        """
        with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._snapshot is not None
                and (now - self._snapshot_time) < SNAPSHOT_TTL_SECONDS
            ):
                return self._snapshot
            try:
                if self._needs_reopen():
                    if self._device is not None:
                        self._close_locked()
                    self._open()
                snap = self._read_snapshot()
                self._snapshot = snap
                self._snapshot_time = now
                self._last_error = None
                return snap
            except Exception as exc:
                self._last_error = str(exc)
                self._close_locked()
                snap = {
                    "connected": False,
                    "mock_mode": bool(self._config.get("mock_mode")),
                    "target": self._config.get("target"),
                    "display": _display_label(
                        bool(self._config.get("mock_mode")),
                        self._config.get("target"),
                    ),
                    "error": str(exc),
                    "info": None,
                    "mode": None,
                    "slots": [],
                }
                self._snapshot = snap
                self._snapshot_time = now
                return snap

    def _read_snapshot(self):
        dev = self._device
        info = dev.info
        try:
            mode = dev.mode
            mode_dict = mode._asdict()
        except Exception:
            mode_dict = None
        slots = dev.film_slots_info()
        return {
            "connected": True,
            "mock_mode": bool(self._opened_mock),
            "target": self._opened_target,
            "display": _display_label(bool(self._opened_mock), self._opened_target),
            "error": None,
            "info": info._asdict() if info is not None else None,
            "mode": mode_dict,
            "slots": slots,
        }

    def install(self, slot, flm_bytes):
        """Upload encrypted FLM bytes to a device slot.

        We bypass pp8k.Device.install() because it expects a parsed FilmTable;
        we already have validated encrypted bytes from the film-table store, so we
        write them through the lower-level backend directly.
        """
        with self._lock:
            if self._needs_reopen():
                self._open()
            self._device._dev.upload_film_table(int(slot), flm_bytes)
            self._snapshot = None


def _display_label(mock_mode, target):
    """Human-readable label for the current connection."""
    if mock_mode:
        return "Mock"
    if target in (None, ""):
        return "Not configured"
    if str(target).isdigit():
        return f"PiSCSI (s2pexec) id {int(target)}"
    return f"SG_IO {target}"


def discover():
    """Best-effort scan for connected ProPalette devices.

    Returns a list of dicts: {"transport", "target", "info"}.
    Never raises -- failures per candidate are silently skipped.
    """
    hits = []

    for path in sorted(glob.glob("/dev/sg*")):
        info = _probe_sgio(path)
        if info is not None:
            hits.append({"transport": "sgio", "target": path, "info": info})

    if shutil.which("s2pexec"):
        for scsi_id in range(8):
            info = _probe_s2pexec(scsi_id)
            if info is not None:
                hits.append({"transport": "s2pexec", "target": scsi_id, "info": info})

    return hits


def _probe_sgio(path):
    try:
        dev = pp8k.open(path)
    except Exception:
        return None
    try:
        info = dev.info
        if info.identification != "DP2SCSI":
            return None
        return info._asdict()
    finally:
        try:
            dev.close()
        except Exception:
            pass


def _probe_s2pexec(scsi_id):
    # Cheap pre-check via s2pexec inquiry so we don't hang on empty IDs.
    try:
        result = subprocess.run(
            ["s2pexec", "-i", str(scsi_id), "-c", "12000000ff00"],
            capture_output=True,
            timeout=2.0,
        )
        if result.returncode != 0:
            return None
        if b"DP2SCSI" not in result.stdout:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    try:
        dev = pp8k.open(scsi_id)
    except Exception:
        return None
    try:
        info = dev.info
        if info.identification != "DP2SCSI":
            return None
        return info._asdict()
    finally:
        try:
            dev.close()
        except Exception:
            pass
