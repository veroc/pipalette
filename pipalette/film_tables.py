"""Film tables: persistent store of uploaded FLM blobs + metadata."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pp8k


# Slot range exposed by pp8k. 0-18 are user-managed; the slot configured
# as scratch is volatile.
SLOT_MIN = 0
SLOT_MAX = 19


class FilmTables:
    """Flat-file store of FLM blobs + a JSON metadata index.

    Layout under `root`:
        files/<sha1>.flm        encrypted FLM bytes
        index.json              { profiles: [...] }
    """

    def __init__(self, root):
        self._root = Path(root)
        self._files_dir = self._root / "files"
        self._index_path = self._root / "index.json"
        self._lock = threading.Lock()
        self._index = {"profiles": []}
        self._load()

    def _load(self):
        self._files_dir.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._save()
            return
        try:
            with self._index_path.open("r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self._index["profiles"] = loaded.get("profiles", [])
        # Old "assignments" key from before rolls existed — drop it on next save.
        had_assignments = "assignments" in loaded
        changed = self._backfill_profiles() or had_assignments
        if changed:
            self._save()

    def _backfill_profiles(self):
        """Older profiles missing aspect/bw_filter — re-parse the FLM once."""
        changed = False
        for p in self._index["profiles"]:
            if all(k in p for k in ("aspect_w", "aspect_h", "bw_filter")):
                continue
            path = self._files_dir / p["filename"]
            if not path.exists():
                continue
            try:
                flm = _parse_flm_bytes(path.read_bytes())
            except Exception:
                continue
            p["aspect_w"] = flm.aspect_w
            p["aspect_h"] = flm.aspect_h
            p["bw_filter"] = flm.bw_filter
            p.setdefault("bw_filter_name", flm.bw_filter_name)
            changed = True
        return changed

    def _save(self):
        self._root.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": self._index["profiles"]}
        tmp = self._index_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self._index_path)

    # ---- queries ---------------------------------------------------------

    def profiles(self):
        with self._lock:
            return [dict(p) for p in self._index["profiles"]]

    def profile(self, profile_id):
        with self._lock:
            return self._find(profile_id)

    def _find(self, profile_id):
        for p in self._index["profiles"]:
            if p["id"] == profile_id:
                return dict(p)
        return None

    def read_bytes(self, profile_id):
        with self._lock:
            profile = self._find(profile_id)
            if profile is None:
                return None
            return (self._files_dir / profile["filename"]).read_bytes()

    def read_table(self, profile_id):
        """Return the parsed pp8k FilmTable for a profile, or None."""
        raw = self.read_bytes(profile_id)
        if raw is None:
            return None
        return _parse_flm_bytes(raw)

    # ---- mutations -------------------------------------------------------

    def add(self, raw_bytes, original_name):
        """Validate and store an uploaded .FLM file. Returns the new profile dict."""
        flm = _parse_flm_bytes(raw_bytes)
        digest = hashlib.sha1(flm.encrypted_data).hexdigest()
        filename = digest + ".flm"
        with self._lock:
            for p in self._index["profiles"]:
                if p["id"] == digest:
                    return dict(p)
            (self._files_dir / filename).write_bytes(flm.encrypted_data)
            profile = {
                "id": digest,
                "filename": filename,
                "original_name": original_name,
                "name": flm.name,
                "camera_type": flm.camera_type_name,
                "is_bw": bool(flm.is_bw),
                "bw_filter": flm.bw_filter,
                "bw_filter_name": flm.bw_filter_name,
                "aspect_w": flm.aspect_w,
                "aspect_h": flm.aspect_h,
                "size": len(flm.encrypted_data),
                "uploaded_at": int(time.time()),
            }
            self._index["profiles"].append(profile)
            self._save()
            return dict(profile)

    def delete(self, profile_id):
        with self._lock:
            profile = self._find(profile_id)
            if profile is None:
                return False
            path = self._files_dir / profile["filename"]
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self._index["profiles"] = [
                p for p in self._index["profiles"] if p["id"] != profile_id
            ]
            self._save()
            return True


def _parse_flm_bytes(raw_bytes):
    """Parse FLM bytes via pp8k.

    Prefers the in-memory entrypoint when available, otherwise routes
    through a temp file.
    """
    if hasattr(pp8k, "load_flm_bytes"):
        return pp8k.load_flm_bytes(raw_bytes)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flm", delete=True) as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        return pp8k.load_flm(tmp.name)
