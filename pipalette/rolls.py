"""Roll store: ordered queue of frames to expose against a snapshotted FLM.

A roll is self-contained: it copies the FLM bytes from the film-table
store at creation time, so a later film-table delete doesn't break it.
Each frame references a source image and carries its own resolution,
transform, rotation, background.  On any of those changing, we re-render
the output (4k/8k canvas at the FLM aspect) and the thumbnail.
"""

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import pp8k
from PIL import Image, ImageOps

# libvips path is optional. When pyvips imports cleanly AND libvips42 is
# actually loadable we use it for the resize/composite — it's multi-threaded
# and 2-4× faster on a Pi for the LANCZOS pass that dominates rendering.
# Otherwise we fall through to PIL with no behaviour change.
_HAS_VIPS = False
try:
    import pyvips as _pyvips
    _pyvips.version(0)  # forces the libvips.so.42 dlopen
    _HAS_VIPS = True
except Exception:
    _pyvips = None


SLOT_MIN = 0
SLOT_MAX = 19

ALLOWED_RESOLUTIONS = ("4k", "8k")
ALLOWED_TRANSFORMS = ("fit", "fill", "1to1")
ALLOWED_ROTATIONS = (0, 90, 180, 270)
ALLOWED_BACKGROUNDS = ("black", "white")
ALLOWED_BW_FILTERS = (1, 2, 3)  # Green, Red, Blue. 0 (Clear) is not selectable.
ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")

THUMB_LONG_EDGE = 240
RESOLUTION_8K_THRESHOLD = 6000  # source long-edge >= this → default to 8k


class Roll:
    """Plain-dict-backed roll record.  Stored under the index's "rolls" list."""


class RollStore:
    """Flat-file store of rolls.

    Layout under `root`:
        index.json
        <roll_id>/
            profile.flm                snapshotted FLM bytes
            images/<image_id>.<ext>    uploaded sources
            outputs/<frame_id>.png     rendered output at FLM aspect × 4k/8k
            thumbs/<frame_id>.jpg      ~240 px downscale of output
    """

    def __init__(self, root):
        self._root = Path(root)
        self._index_path = self._root / "index.json"
        self._lock = threading.Lock()
        self._index = {"rolls": []}
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self):
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._save()
            return
        try:
            with self._index_path.open("r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self._index["rolls"] = loaded.get("rolls", [])

    def _save(self):
        tmp = self._index_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(self._index, f, indent=2)
        os.replace(tmp, self._index_path)

    def _find(self, roll_id):
        for r in self._index["rolls"]:
            if r["id"] == roll_id:
                return r
        return None

    def _roll_dir(self, roll_id):
        return self._root / roll_id

    # ---- queries --------------------------------------------------------

    def list(self, include_calibration=False):
        """Return summaries for user-facing rolls.

        Calibration rolls (those with `calibration_for` set) are internal
        artefacts of the calibration workflow and are hidden from the
        normal rolls listing by default."""
        with self._lock:
            return [
                self._summary(r) for r in self._index["rolls"]
                if include_calibration or not r.get("calibration_for")
            ]

    def get_calibration_roll(self, profile_id):
        """Return the active calibration roll for a film-table profile, or None."""
        with self._lock:
            for r in self._index["rolls"]:
                if r.get("calibration_for") == profile_id:
                    return _deep_copy_json(r)
            return None

    def get(self, roll_id):
        with self._lock:
            roll = self._find(roll_id)
            return None if roll is None else _deep_copy_json(roll)

    def size(self, roll_id):
        """Total disk footprint of the roll's folder, in bytes."""
        roll_dir = self._roll_dir(roll_id)
        if not roll_dir.exists():
            return 0
        total = 0
        for path in roll_dir.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
        return total

    @staticmethod
    def _summary(roll):
        frames = roll.get("frames", [])
        counts = {"pending": 0, "exposing": 0, "done": 0, "failed": 0}
        for f in frames:
            counts[f["status"]] = counts.get(f["status"], 0) + 1
        return {
            "id": roll["id"],
            "name": roll["name"],
            "created_at": roll["created_at"],
            "status": roll["status"],
            "profile_name": roll["profile_name"],
            "camera_type": roll["camera_type"],
            "is_bw": roll["is_bw"],
            "aspect_w": roll["aspect_w"],
            "aspect_h": roll["aspect_h"],
            "bw_filter": roll.get("bw_filter"),
            "skip_calibration": roll.get("skip_calibration", False),
            "frame_count": len(frames),
            "counts": counts,
        }

    # ---- mutations: rolls ----------------------------------------------

    def create(self, name, profile, flm_bytes, bw_filter=None,
               calibration_for=None, calibration_mode=None):
        """Create a roll from a film-table profile + its FLM bytes.

        `profile` is the dict returned by FilmTables — we snapshot its
        FLM-derived fields.  `flm_bytes` is the original encrypted blob.
        For B&W tables, `bw_filter` is required (1/2/3 = Green/Red/Blue)
        and locked for the lifetime of the roll.  For color tables it
        is ignored.  Exposure runs via `pp8k.Device.expose(flm=...)`
        which uses pp8k's internal scratch slot, so the roll doesn't
        carry a device slot at all.

        If `calibration_for` is set to a film-table profile id, the
        roll is marked as a calibration session for that film table --
        the UI uses this to show the measurement-entry workflow.
        `calibration_mode` distinguishes "speed_point" (first-time
        wedge through a known calibration LUT) from "refinement"
        (31-step wedge through the target FLM).  Defaults to
        "refinement" if a calibration is being created without an
        explicit mode (backward compat with rolls that predate the
        speed-point flow).
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        is_bw = bool(profile.get("is_bw"))
        if is_bw:
            if bw_filter is None:
                raise ValueError("bw_filter is required for B&W film tables")
            bw_filter = int(bw_filter)
            if bw_filter not in ALLOWED_BW_FILTERS:
                raise ValueError("bw_filter must be 1/2/3 (Green/Red/Blue)")
        else:
            bw_filter = None

        roll_id = uuid.uuid4().hex[:12]
        roll_dir = self._roll_dir(roll_id)
        (roll_dir / "images").mkdir(parents=True, exist_ok=True)
        (roll_dir / "outputs").mkdir(parents=True, exist_ok=True)
        (roll_dir / "thumbs").mkdir(parents=True, exist_ok=True)
        (roll_dir / "profile.flm").write_bytes(flm_bytes)

        roll = {
            "id": roll_id,
            "name": name.strip(),
            "created_at": int(time.time()),
            "status": "planned",
            "profile_id": profile.get("id"),
            "profile_name": profile.get("name") or "(unnamed)",
            "camera_type": profile.get("camera_type") or "",
            "is_bw": is_bw,
            "aspect_w": int(profile["aspect_w"]),
            "aspect_h": int(profile["aspect_h"]),
            "bw_filter": bw_filter,
            "skip_calibration": False,
            "recalibrate_next": True,
            "frames": [],
        }
        if calibration_for:
            roll["calibration_for"] = calibration_for
            roll["calibration_mode"] = calibration_mode or "refinement"
            roll["measurements"] = []  # filled when the user enters densities
        with self._lock:
            self._index["rolls"].append(roll)
            self._save()
        return _deep_copy_json(roll)

    def rename(self, roll_id, name):
        if not name or not name.strip():
            raise ValueError("name is required")
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            roll["name"] = name.strip()
            self._save()
            return _deep_copy_json(roll)

    def update(self, roll_id, **changes):
        """Update mutable roll-wide options. `bw_filter` is intentionally
        not editable here — it's locked at create time."""
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            if "skip_calibration" in changes:
                roll["skip_calibration"] = bool(changes["skip_calibration"])
            self._save()
            return _deep_copy_json(roll)

    def delete(self, roll_id):
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                return False
            self._index["rolls"] = [r for r in self._index["rolls"] if r["id"] != roll_id]
            self._save()
        shutil.rmtree(self._roll_dir(roll_id), ignore_errors=True)
        return True

    # ---- mutations: frames ---------------------------------------------

    def add_image(self, roll_id, raw_bytes, original_name):
        """Upload an image, append a new frame, render output + thumb."""
        ext = _ext_from_name(original_name)
        if ext not in ALLOWED_IMAGE_EXTS:
            raise ValueError(f"unsupported image format {ext!r}")
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)

            roll_dir = self._roll_dir(roll_id)
            image_id = hashlib.sha1(raw_bytes).hexdigest()[:16] + "_" + uuid.uuid4().hex[:6]
            image_filename = image_id + ext
            image_path = roll_dir / "images" / image_filename
            image_path.write_bytes(raw_bytes)

            try:
                src_w, src_h = _read_image_size(image_path)
            except Exception as exc:
                image_path.unlink(missing_ok=True)
                raise ValueError(f"cannot read image: {exc}")

            default_resolution = "8k" if max(src_w, src_h) >= RESOLUTION_8K_THRESHOLD else "4k"
            frame_id = uuid.uuid4().hex[:12]
            frame = {
                "id": frame_id,
                "image_id": image_id,
                "image_filename": image_filename,
                "original_name": original_name,
                "src_width": src_w,
                "src_height": src_h,
                "order": len(roll["frames"]),
                "resolution": default_resolution,
                "transform": "fit",
                "rotation": 0,
                "background": "black",
                "status": "pending",
                "exposed_at": None,
                "exposure_count": 0,
                "last_error": None,
                "transform_warning": None,
            }
            roll["frames"].append(frame)
            self._save()
            self._render_frame_outputs(roll, frame)
            return _deep_copy_json(frame)

    def update_frame(self, roll_id, frame_id, **changes):
        """Update per-frame settings; re-render if anything affecting output changes."""
        regen_keys = {"resolution", "transform", "rotation", "background"}
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            frame = _find_frame(roll, frame_id)
            if frame is None:
                raise KeyError(frame_id)

            dirty = False
            if "resolution" in changes:
                v = changes["resolution"]
                if v not in ALLOWED_RESOLUTIONS:
                    raise ValueError("resolution must be '4k' or '8k'")
                if frame["resolution"] != v:
                    frame["resolution"] = v
                    dirty = True
            if "transform" in changes:
                v = changes["transform"]
                if v not in ALLOWED_TRANSFORMS:
                    raise ValueError("transform must be 'fit', 'fill', or '1to1'")
                if frame["transform"] != v:
                    frame["transform"] = v
                    dirty = True
            if "rotation" in changes:
                v = int(changes["rotation"])
                if v not in ALLOWED_ROTATIONS:
                    raise ValueError("rotation must be 0/90/180/270")
                if frame["rotation"] != v:
                    frame["rotation"] = v
                    dirty = True
            if "background" in changes:
                v = changes["background"]
                if v not in ALLOWED_BACKGROUNDS:
                    raise ValueError("background must be 'black' or 'white'")
                if frame["background"] != v:
                    frame["background"] = v
                    dirty = True
            if dirty and any(k in changes for k in regen_keys):
                self._render_frame_outputs(roll, frame)
            self._save()
            return _deep_copy_json(frame)

    def delete_frame(self, roll_id, frame_id):
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            frame = _find_frame(roll, frame_id)
            if frame is None:
                return False
            roll_dir = self._roll_dir(roll_id)
            _safe_unlink(roll_dir / "outputs" / (frame_id + ".png"))
            _safe_unlink(roll_dir / "thumbs" / (frame_id + ".jpg"))
            # If no other frame references the source image, drop the original too.
            image_id = frame["image_id"]
            roll["frames"] = [f for f in roll["frames"] if f["id"] != frame_id]
            if not any(f["image_id"] == image_id for f in roll["frames"]):
                _safe_unlink(roll_dir / "images" / frame["image_filename"])
            for idx, f in enumerate(roll["frames"]):
                f["order"] = idx
            self._save()
            return True

    def set_frame_status(self, roll_id, frame_id, status, error=None,
                         mark_exposed=False):
        """Update a frame's status fields atomically. No re-render.

        - `status`: pending | exposing | done | failed | skipped
        - `error`: stored in `last_error`; pass None to clear it.
        - `mark_exposed=True`: bump exposure_count and stamp exposed_at.
        """
        allowed = ("pending", "exposing", "done", "failed", "skipped")
        if status not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            frame = _find_frame(roll, frame_id)
            if frame is None:
                raise KeyError(frame_id)
            frame["status"] = status
            frame["last_error"] = error
            if mark_exposed:
                frame["exposure_count"] = int(frame.get("exposure_count", 0)) + 1
                frame["exposed_at"] = int(time.time())
            self._save()
            return _deep_copy_json(frame)

    def set_measurements(self, roll_id, measurements):
        """Store the densitometer measurements for a calibration roll.

        `measurements` is a list of dicts {"pixel": int, "density": float,
        "resolution"?: "4k"|"8k"} or a list of (pixel, density) tuples.
        The optional `resolution` field is preserved so the apply step
        can split 4K (Master A) from 8K (Master B) data correctly.
        """
        normalized = []
        for m in measurements:
            if isinstance(m, dict):
                entry = {"pixel": int(m["pixel"]),
                         "density": float(m["density"])}
                if m.get("resolution"):
                    entry["resolution"] = m["resolution"]
                normalized.append(entry)
            else:
                px, d = m
                normalized.append({"pixel": int(px), "density": float(d)})
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            if "calibration_for" not in roll:
                raise ValueError("roll is not a calibration roll")
            roll["measurements"] = normalized
            self._save()
            return _deep_copy_json(roll)

    def set_roll_field(self, roll_id, key, value):
        """Update a single mutable roll-wide field atomically.

        Used by the exposure runner for `recalibrate_next` between frames.
        """
        allowed = ("recalibrate_next", "skip_calibration", "status")
        if key not in allowed:
            raise ValueError(f"field {key} is not directly settable")
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            roll[key] = value
            self._save()

    def reset_exposing_frames(self):
        """On startup, any 'exposing' frame is from a dead runner — mark failed.

        Called once at app boot so the UI doesn't show ghost spinners after
        a service restart mid-exposure.
        """
        with self._lock:
            dirty = False
            for roll in self._index["rolls"]:
                for frame in roll.get("frames", []):
                    if frame.get("status") == "exposing":
                        frame["status"] = "failed"
                        frame["last_error"] = "Service restarted during exposure"
                        dirty = True
            if dirty:
                self._save()

    def roll_dir(self, roll_id):
        """Public accessor for the on-disk roll directory."""
        return self._roll_dir(roll_id)

    def reorder(self, roll_id, frame_ids):
        """Set frame order from a full list of frame_ids."""
        with self._lock:
            roll = self._find(roll_id)
            if roll is None:
                raise KeyError(roll_id)
            existing = {f["id"]: f for f in roll["frames"]}
            if set(frame_ids) != set(existing.keys()):
                raise ValueError("frame_ids must be a permutation of existing frames")
            new_frames = []
            for idx, fid in enumerate(frame_ids):
                f = existing[fid]
                f["order"] = idx
                new_frames.append(f)
            roll["frames"] = new_frames
            self._save()

    # ---- assets ---------------------------------------------------------

    def asset_path(self, roll_id, kind, name):
        """Resolve a roll asset path safely.

        kind ∈ {"images", "outputs", "thumbs", "profile"}.
        """
        if kind == "profile":
            return self._roll_dir(roll_id) / "profile.flm"
        if kind not in ("images", "outputs", "thumbs"):
            return None
        base = (self._roll_dir(roll_id) / kind).resolve()
        candidate = (base / name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate if candidate.exists() else None

    # ---- rendering ------------------------------------------------------

    def _render_frame_outputs(self, roll, frame):
        """Render the exposure-ready output PNG + the thumbnail JPEG.

        Mirrors pp8k.imaging.image_to_scanlines spatial behaviour so
        the on-disk output matches what the device would receive.

        Dispatches to the libvips implementation when available
        (multi-threaded, 2-4× faster on ARM), with a PIL fallback.
        """
        roll_dir = self._roll_dir(roll["id"])
        src = roll_dir / "images" / frame["image_filename"]
        out_path = roll_dir / "outputs" / (frame["id"] + ".png")
        thumb_path = roll_dir / "thumbs" / (frame["id"] + ".jpg")

        width, height = pp8k.get_frame_dimensions(
            roll["aspect_w"], roll["aspect_h"], frame["resolution"]
        )

        if _HAS_VIPS:
            try:
                self._render_vips(src, out_path, thumb_path, width, height, frame)
                return
            except Exception as exc:
                # Don't lose the frame because of a libvips quirk — fall
                # through to PIL on any vips error.
                print(f"[render] pyvips path failed for {frame['id']}: "
                      f"{type(exc).__name__}: {exc}; falling back to PIL",
                      flush=True)
        self._render_pil(src, out_path, thumb_path, width, height, frame)

    def _render_pil(self, src, out_path, thumb_path, width, height, frame):
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img) or img
            if frame["rotation"] == 90:
                img = img.transpose(Image.ROTATE_270)
            elif frame["rotation"] == 180:
                img = img.transpose(Image.ROTATE_180)
            elif frame["rotation"] == 270:
                img = img.transpose(Image.ROTATE_90)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            bg = (0, 0, 0) if frame["background"] == "black" else (255, 255, 255)
            canvas = Image.new("RGB", (width, height), bg)

            # Cleared on every render — a previous 1:1 crop might no longer apply.
            frame["transform_warning"] = None

            if frame["transform"] == "1to1":
                src_w, src_h = img.width, img.height
                if src_w > width or src_h > height:
                    # Center-crop so the placed image is exactly the canvas
                    # in the over-sized dimension(s); under-sized dimensions
                    # pass through and get background padding.
                    crop_w = min(src_w, width)
                    crop_h = min(src_h, height)
                    left = (src_w - crop_w) // 2
                    top = (src_h - crop_h) // 2
                    placed = img.crop((left, top, left + crop_w, top + crop_h))
                    frame["transform_warning"] = (
                        f"Source {src_w}×{src_h} px exceeds the "
                        f"{frame['resolution'].upper()} canvas "
                        f"({width}×{height} px) — center-cropped."
                    )
                else:
                    placed = img
                new_w, new_h = placed.width, placed.height
            else:
                img_ratio = img.width / img.height
                frame_ratio = width / height
                if frame["transform"] == "fill":
                    if img_ratio > frame_ratio:
                        new_h = height
                        new_w = round(height * img_ratio)
                    else:
                        new_w = width
                        new_h = round(width / img_ratio)
                else:  # fit
                    if img_ratio > frame_ratio:
                        new_w = width
                        new_h = round(width / img_ratio)
                    else:
                        new_h = height
                        new_w = round(height * img_ratio)
                placed = img.resize((new_w, new_h), Image.LANCZOS)

            x = (width - new_w) // 2
            y = (height - new_h) // 2
            canvas.paste(placed, (x, y))

            canvas.save(out_path, "PNG", optimize=False, compress_level=1)

            # BICUBIC for the thumb is visually indistinguishable from LANCZOS
            # at 240 px and substantially faster — saves a heavy resampling
            # pass on the just-rendered 4K/8K canvas.
            thumb = canvas.copy()
            thumb.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.BICUBIC)
            if thumb.mode != "RGB":
                thumb = thumb.convert("RGB")
            thumb.save(thumb_path, "JPEG", quality=82, optimize=True)

    def _render_vips(self, src, out_path, thumb_path, width, height, frame):
        """libvips path — same geometry as _render_pil, multi-threaded.

        libvips composes a lazy pipeline; the heavy lifting only runs at
        write_to_file time, allowing the resize / embed / save to fuse.

        Note: we *don't* set access="sequential". `autorot` and per-frame
        rotation (rot90/270) need to read the source out of top-to-bottom
        order, which a sequential JPEG decoder can't service — it errors
        with "out of order read at line N". The default random-access
        mode handles every input cleanly at the cost of higher peak RAM.
        """
        img = _pyvips.Image.new_from_file(str(src))
        img = img.autorot()  # respect EXIF orientation (PIL ImageOps.exif_transpose equivalent)

        # libvips rot90() is clockwise; matches our frame.rotation semantics.
        if frame["rotation"] == 90:
            img = img.rot90()
        elif frame["rotation"] == 180:
            img = img.rot180()
        elif frame["rotation"] == 270:
            img = img.rot270()

        # Force sRGB — handles grayscale / CMYK / other spaces into 3-band RGB.
        img = img.colourspace("srgb")
        # Drop alpha if present (matches PIL convert("RGB") for RGBA).
        if img.hasalpha():
            img = img.extract_band(0, n=3)

        bg_pixel = [0, 0, 0] if frame["background"] == "black" else [255, 255, 255]

        # Cleared on every render — a previous 1:1 crop might no longer apply.
        frame["transform_warning"] = None

        if frame["transform"] == "1to1":
            src_w, src_h = img.width, img.height
            if src_w > width or src_h > height:
                crop_w = min(src_w, width)
                crop_h = min(src_h, height)
                left = (src_w - crop_w) // 2
                top = (src_h - crop_h) // 2
                img = img.crop(left, top, crop_w, crop_h)
                frame["transform_warning"] = (
                    f"Source {src_w}×{src_h} px exceeds the "
                    f"{frame['resolution'].upper()} canvas "
                    f"({width}×{height} px) — center-cropped."
                )
        else:
            img_ratio = img.width / img.height
            frame_ratio = width / height
            if frame["transform"] == "fill":
                if img_ratio > frame_ratio:
                    factor = height / img.height
                else:
                    factor = width / img.width
            else:  # fit
                if img_ratio > frame_ratio:
                    factor = width / img.width
                else:
                    factor = height / img.height
            img = img.resize(factor, kernel="lanczos3")

        # Place the (possibly oversized in 'fill') image onto the canvas.
        # For fill: centre-crop the overflow. For fit / 1:1: centre with bg.
        if frame["transform"] == "fill" and (img.width > width or img.height > height):
            left = (img.width - width) // 2
            top = (img.height - height) // 2
            img = img.crop(left, top, width, height)
        else:
            x = (width - img.width) // 2
            y = (height - img.height) // 2
            img = img.embed(
                x, y, width, height,
                extend="background", background=bg_pixel,
            )

        # Write the exposure PNG at zlib level 1 (matches the PIL path).
        # We then thumbnail straight from the just-written file: the OS
        # page cache keeps the bytes hot, and libvips uses libpng's
        # shrink-on-load to decode only the rows it needs.
        img.write_to_file(str(out_path), compression=1)
        thumb = _pyvips.Image.thumbnail(
            str(out_path), THUMB_LONG_EDGE, height=THUMB_LONG_EDGE,
        )
        thumb.write_to_file(str(thumb_path), Q=82)


# ---- helpers ------------------------------------------------------------


def _ext_from_name(name):
    name = (name or "").lower()
    for ext in ALLOWED_IMAGE_EXTS:
        if name.endswith(ext):
            return ext
    return ""


def _read_image_size(path):
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img) or img
        return img.width, img.height


def _find_frame(roll, frame_id):
    for f in roll["frames"]:
        if f["id"] == frame_id:
            return f
    return None


def _safe_unlink(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _deep_copy_json(value):
    return json.loads(json.dumps(value))
