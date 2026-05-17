"""Film tables: persistent store of FLM blobs + metadata."""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import pp8k

from . import wizard_baselines


# Internal-name validation: 1-8 chars, ASCII letters/digits/'-'/'_'.  The FLM
# field is 8 bytes max (pp8k truncates and null-pads); we forbid spaces and
# special chars so the name is also usable as a filename and as a URL slug.
_VALID_INTERNAL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,8}$")
_SANITIZE_INTERNAL_NAME = re.compile(r"[^A-Za-z0-9_-]")


# Slot range exposed by pp8k. 0-18 are user-managed; the slot configured
# as scratch is volatile.
SLOT_MIN = 0
SLOT_MAX = 19


# Calibration provenance carried per profile.  Drives the dual-mode
# calibration UI: speed-point calibration is always available, but
# refinement is gated until a speed-point round has run (or the user
# has explicitly chosen to skip it).
CAL_STATE_UNCALIBRATED = "uncalibrated"
CAL_STATE_SPEED_POINT = "speed_point"
CAL_STATE_REFINED = "refined"
CAL_STATES = (CAL_STATE_UNCALIBRATED, CAL_STATE_SPEED_POINT, CAL_STATE_REFINED)


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
        backfilled = self._backfill_profiles()
        migrated = self._migrate_to_internal_name_keys()
        relabeled = self._refresh_bw_filter_labels()
        cal_state_added = self._backfill_cal_state()
        if backfilled or migrated or relabeled or had_assignments or cal_state_added:
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

    def _backfill_cal_state(self):
        """Default missing cal_state to 'uncalibrated' on existing profiles.

        Profiles uploaded or wizard-created before this field was added
        don't carry any calibration provenance.  The conservative choice
        is to mark them uncalibrated so the UI prompts the user to run
        speed-point calibration before enabling refinement.  The user can
        skip if the profile is already known-good in their workflow.
        """
        changed = False
        for p in self._index["profiles"]:
            if "cal_state" not in p:
                p["cal_state"] = CAL_STATE_UNCALIBRATED
                changed = True
        return changed

    def _refresh_bw_filter_labels(self):
        """Re-derive cached bw_filter_name from the (correct) local table.

        pp8k's BW_FILTER_NAMES has values 0 and 2 swapped vs the DPAL
        toolkit; older profiles uploaded before this was fixed have stale
        labels.  This refresh is cheap and idempotent.
        """
        changed = False
        for p in self._index["profiles"]:
            if "bw_filter" not in p:
                continue
            expected = _bw_filter_label(p["bw_filter"])
            if p.get("bw_filter_name") != expected:
                p["bw_filter_name"] = expected
                changed = True
        return changed

    def _migrate_to_internal_name_keys(self):
        """One-shot: re-key SHA-1-named profiles to <internal_name>.flm.

        Detects 40-hex `id` values, reads `internal_name` from the FLM,
        sanitizes (`-2`/`-3`/... on collision), renames the file, updates
        the profile entry.  Idempotent: profiles already on internal_name
        keys are left alone.
        """
        changed = False
        taken = {
            p["id"] for p in self._index["profiles"]
            if not _is_sha1(p["id"])
        }
        for p in self._index["profiles"]:
            if not _is_sha1(p["id"]):
                continue
            path = self._files_dir / p["filename"]
            if not path.exists():
                continue
            try:
                flm = _parse_flm_bytes(path.read_bytes())
            except Exception:
                continue
            new_id = _sanitize_internal_name(flm.internal_name) or p["id"][:8]
            new_id = _disambiguate(new_id, taken)
            new_filename = new_id + ".flm"
            new_path = self._files_dir / new_filename
            try:
                path.rename(new_path)
            except OSError:
                continue
            p["id"] = new_id
            p["filename"] = new_filename
            taken.add(new_id)
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
                "cal_state": CAL_STATE_UNCALIBRATED,
            }
            self._index["profiles"].append(profile)
            self._save()
            return dict(profile)

    def create(self, *, name, internal_name, is_color, bw_filter,
               camera_type, iso):
        """Build a new FLM from the wizard inputs.  Returns the new profile.

        Only B&W 35mm is supported in v1 (the only baseline we ship).  Color
        and non-35mm formats are reserved for v2 and rejected here.

        Raises ValueError on invalid input or internal_name collision.
        """
        # ---- validate ----
        name = (name or "").strip()
        if not 1 <= len(name) <= 24:
            raise ValueError("name must be 1-24 chars")
        if not isinstance(internal_name, str) or not _VALID_INTERNAL_NAME.match(internal_name):
            raise ValueError(
                "internal_name must be 1-8 chars [A-Za-z0-9_-]"
            )
        if is_color:
            raise ValueError("color FLMs are not supported yet (v1: B&W only)")
        if camera_type != 1:
            raise ValueError("only 35mm (camera_type=1) is supported (v1)")
        # Allow only the three single-phosphor filters; byte 0 is "Clear"
        # = 3-pass mode (not appropriate for the B&W wizard path).
        if bw_filter not in (1, 2, 3):
            raise ValueError(
                "bw_filter must be 1 (Green), 2 (Red), or 3 (Blue)"
            )
        if iso not in wizard_baselines._BASE_BY_ISO:
            raise ValueError(
                f"iso must be one of {sorted(wizard_baselines._BASE_BY_ISO)}"
            )

        with self._lock:
            # Collision check on internal_name.
            for p in self._index["profiles"]:
                if p["id"] == internal_name:
                    raise ValueError(
                        f"internal_name {internal_name!r} already in use"
                    )

            # ---- build the FilmTable via pp8k ----
            sets = wizard_baselines.build_bw_35mm_lut_sets(iso)
            aspect_w, aspect_h = 3, 2  # 35mm
            flags = 0x10 | ((bw_filter & 0x03) << 2)
            table = pp8k.FilmTable(
                name=name[:24],
                internal_name=internal_name,
                camera_type=camera_type,
                camera_type_name="35mm",
                is_bw=True,
                bw_filter=bw_filter,
                bw_filter_name=_bw_filter_label(bw_filter),
                aspect_w=aspect_w,
                aspect_h=aspect_h,
                lut_sets=sets,
                encrypted_data=b"",
                flags=flags,
                raw_extended=wizard_baselines.raw_extended_for(iso),
            )
            # normalize_masters is a no-op on freshly built tables (the
            # invariants already hold), but run it anyway to make the
            # invariant explicit and survive any future construction tweaks.
            table = pp8k.normalize_masters(table)
            raw = pp8k.serialize_flm(table)

            filename = internal_name + ".flm"
            (self._files_dir / filename).write_bytes(raw)
            profile = {
                "id": internal_name,
                "filename": filename,
                "original_name": filename,
                "name": name,
                "camera_type": "35mm",
                "is_bw": True,
                "bw_filter": bw_filter,
                "bw_filter_name": _bw_filter_label(bw_filter),
                "aspect_w": aspect_w,
                "aspect_h": aspect_h,
                "size": len(raw),
                "iso": iso,
                "uploaded_at": int(time.time()),
                "cal_state": CAL_STATE_UNCALIBRATED,
            }
            self._index["profiles"].append(profile)
            self._save()
            return dict(profile)

    def set_cal_state(self, profile_id, state):
        """Update the cal_state for a profile.  Used by the calibration
        apply endpoints after a successful round."""
        if state not in CAL_STATES:
            raise ValueError(f"invalid cal_state {state!r}; "
                             f"valid: {CAL_STATES}")
        with self._lock:
            for p in self._index["profiles"]:
                if p["id"] == profile_id:
                    p["cal_state"] = state
                    self._save()
                    return dict(p)
            raise KeyError(profile_id)

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


# B&W filter byte values, verified against pp8k's flag breakdown (which
# matches the byte-0 = 3-pass "Clear" mode observed on the real device
# during the 2026-05-16 BFB test): bit 4 = is_bw; bits 2-3 select filter.
# The DPAL toolkit's RED/GREEN/BLUE constants are for color-pass indexing
# (which phosphor each of the three color passes drives), NOT for the B&W
# filter byte enum -- those are different name spaces.
_BW_FILTER_LABELS = {0: "Clear", 1: "Green", 2: "Red", 3: "Blue"}


def _bw_filter_label(value):
    return _BW_FILTER_LABELS.get(value, f"Unknown({value})")


def _is_sha1(value):
    return isinstance(value, str) and len(value) == 40 and all(
        c in "0123456789abcdef" for c in value
    )


def _sanitize_internal_name(name):
    """Strip non-[A-Za-z0-9_-] chars; trim to 8 chars; return '' if empty."""
    if not isinstance(name, str):
        return ""
    cleaned = _SANITIZE_INTERNAL_NAME.sub("", name).strip("-_")[:8]
    return cleaned


def _disambiguate(base, taken):
    """Return `base` if free, otherwise `base-2`, `base-3`, ... fitting <= 8 chars."""
    if base and base not in taken:
        return base
    # Keep some room for the suffix; we accept up to 7 chars of base.
    stem = (base or "fmprofil")[:6]
    n = 2
    while True:
        candidate = f"{stem}-{n}"[:8]
        if candidate not in taken:
            return candidate
        n += 1


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
