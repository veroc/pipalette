"""Persistent app configuration (JSON-backed)."""

import json
import os
import threading
from pathlib import Path


DEFAULTS = {
    # If True, use pp8k.mock() for development.  Otherwise, real hardware:
    # pp8k.open(target) dispatches by target type (int → s2pexec, path → sgio).
    "mock_mode": False,
    # Connection target for hardware mode.  An int SCSI ID (PiSCSI HAT path,
    # via s2pexec) or a /dev/sg* path (regular HBA).  None until discovered or set.
    "target": None,
}


class Config:
    """JSON file persisted under data/config.json."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            with self._path.open("r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # One-time migration from the old "transport" string field.
        migrated = False
        if "transport" in loaded and "mock_mode" not in loaded:
            self._values["mock_mode"] = loaded["transport"] == "mock"
            migrated = True
        # Drop fields that no longer exist on the next save.
        for legacy in ("transport", "working_slot", "scratch_slot", "auto_resync"):
            if legacy in loaded:
                migrated = True
        for key, value in loaded.items():
            if key in DEFAULTS:
                self._values[key] = value
        if migrated:
            self._save()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(self._values, f, indent=2)
        os.replace(tmp, self._path)

    def get(self, key):
        return self._values.get(key, DEFAULTS.get(key))

    def all(self):
        return dict(self._values)

    def update(self, **changes):
        with self._lock:
            for key, value in changes.items():
                if key in DEFAULTS:
                    self._values[key] = value
            self._save()
        return self.all()
