"""B&W film-curve calibration via empirical inversion.

Given a 31-step wedge exposure and the measured negative density at each
step, this module:

1. Diagnoses whether the development reaches the working range required
   for a grade-2 print (default 1.05 log density), and whether any
   shortfall is LUT-fixable, chemistry-limited (shouldered), or a global
   underexposure that wants an EI bump instead.
2. Computes a corrected Master A curve by empirical inversion: at each
   input pixel value X, the new LUT value is the OLD LUT value at the
   input that the measured response yielded the target density for.
   Target densities are linear in pixel value:
       D_target(X) = b+f + (X / 255) * target_range
3. Builds a new pp8k.FilmTable with the corrected Master A (and Master B
   tracked at 0.5x display) and returns it ready to serialize.

The math is independent of pp8k internals -- inputs are the OLD LUT as a
256-element display-space array, outputs are NEW LUT in the same space.
The pp8k stored/scale encoding happens at the call site (same logic as
the wizard).
"""

import math

import pp8k

from . import wizard_baselines


# Grade-2 paper handles ~1.05 log density (ISO range).  Other grades:
# 0 -> 1.60, 1 -> 1.40, 2 -> 1.20-1.05, 3 -> 1.05-0.95, 4 -> 0.85, 5 -> 0.70.
# We use 1.05 as the conservative "grade 2 hard" anchor.
PAPER_GRADE_RANGE = {0: 1.60, 1: 1.40, 2: 1.05, 3: 0.95, 4: 0.85, 5: 0.70}

# Speed-point offset above b+f (ISO 6:1993 standard).
SPEED_POINT_OFFSET = 0.10

# Default test wedge: 31 patches spanning the 8-bit input range linearly.
# Matches Stouffer T3110 granularity (0.10 log density per step when the
# LUT lands correctly across the 3.0 range).
WEDGE_STEP_COUNT = 31


def wedge_pixel_values(step_count=WEDGE_STEP_COUNT):
    """Return the input pixel values used by the calibration wedge.

    Linear span from 0 to 255 across `step_count` patches; the first
    patch is at pixel 0 (paper-black anchor) and the last is at pixel
    255 (max input).  For 31 steps the spacing is 8.5 pixel values per
    step -- the resulting density distribution after the LUT and the
    film is what we measure."""
    if step_count < 2:
        raise ValueError("step_count must be >= 2")
    return tuple(
        round(i * 255 / (step_count - 1)) for i in range(step_count)
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnose(measurements, target_range=PAPER_GRADE_RANGE[2]):
    """Inspect the measured densities; classify the response.

    `measurements` is a sequence of (pixel_value, density) tuples in
    increasing pixel order.  Returns a dict with:
        b_plus_f          baseline density (min observed)
        d_max             maximum observed density
        working_range     d_max - (b+f + 0.10)
        target_range      desired range (input)
        verdict           "ok" | "lut_fixable" | "shouldered" |
                          "global_underexposure"
        shortfall         max(0, target_range - working_range)
        time_multiplier   suggested dev-time multiplier (only if
                          shouldered)
        ei_multiplier     suggested EI multiplier (only if
                          global_underexposure; <1 means lower EI)
        speed_point_pixel input X where D crosses b+f + 0.10 (None if
                          never crossed)
    """
    if len(measurements) < 5:
        raise ValueError("need at least 5 measurements")

    px = [m[0] for m in measurements]
    dens = [m[1] for m in measurements]
    b_plus_f = min(dens)
    d_max = max(dens)
    speed_density = b_plus_f + SPEED_POINT_OFFSET
    working_range = d_max - speed_density

    # Speed point: where density first crosses b+f + 0.10.
    speed_pixel = None
    for i in range(len(px) - 1):
        if dens[i] <= speed_density <= dens[i + 1] and dens[i + 1] > dens[i]:
            t = (speed_density - dens[i]) / (dens[i + 1] - dens[i])
            speed_pixel = px[i] + t * (px[i + 1] - px[i])
            break

    # Slope at the top: average of last three pairwise slopes (per pixel).
    # If shallow, the film has shouldered.
    top_n = 3
    top_slopes = []
    for i in range(len(px) - top_n, len(px) - 1):
        dx = px[i + 1] - px[i]
        if dx > 0:
            top_slopes.append((dens[i + 1] - dens[i]) / dx)
    top_slope = sum(top_slopes) / len(top_slopes) if top_slopes else 0.0

    # Middle-range slope for shoulder comparison.
    mid_lo = len(px) // 3
    mid_hi = 2 * len(px) // 3
    mid_slopes = []
    for i in range(mid_lo, mid_hi):
        dx = px[i + 1] - px[i]
        if dx > 0:
            mid_slopes.append((dens[i + 1] - dens[i]) / dx)
    mid_slope = sum(mid_slopes) / len(mid_slopes) if mid_slopes else 0.0

    # Heuristic: top slope < 20% of mid slope => shouldered.
    shouldered = mid_slope > 0 and top_slope < 0.2 * mid_slope

    shortfall = max(0.0, target_range - working_range)

    # Per-step error vs target curve (detects shape problems even when
    # the overall range is correct, e.g. toe shortfall + correct d_max).
    target_total = target_range + SPEED_POINT_OFFSET
    max_step_err = 0.0
    for px_val, d_meas in measurements:
        target_d = b_plus_f + (px_val / 255.0) * target_total
        err = abs(d_meas - target_d)
        if err > max_step_err:
            max_step_err = err

    # Verdict selection.
    if shortfall < 0.03 and max_step_err < 0.05:
        verdict = "ok"
        time_multiplier = None
        ei_multiplier = None
    else:
        # Check for uniform offset (global underexposure): does shifting
        # the input axis line every measured density up with its target?
        # Look at offsets in the *middle* of the response curve only --
        # near the edges, target densities can fall outside the measured
        # range and the inverse-lookup clips, which we'd misread as a
        # smaller spread.
        offsets = []
        for px_val, target_d in _target_pairs(px, b_plus_f, target_range):
            inv_x = _inverse_at(dens, px, target_d)
            if inv_x is None: continue
            # Skip points where inv_x hit the clip at either edge.
            if inv_x <= px[0] + 0.5 or inv_x >= px[-1] - 0.5: continue
            offsets.append(inv_x - px_val)
        # Tight spread of offsets => uniform shift => global EI issue.
        if len(offsets) >= 3 and (max(offsets) - min(offsets)) < 8:
            verdict = "global_underexposure"
            # Average input-axis shift -> EI multiplier estimate.
            # If the curve sits 20 pixels higher than the target, input
            # needs ~20/255 less to land right -- equivalent to lowering
            # EI by (255 + avg_shift) / 255.
            avg_shift = sum(offsets) / len(offsets)
            ei_multiplier = max(0.25, 1.0 / (1.0 + avg_shift / 255.0))
            time_multiplier = None
        elif shouldered:
            verdict = "shouldered"
            time_multiplier = target_range / working_range if working_range > 0 else None
            ei_multiplier = None
        else:
            verdict = "lut_fixable"
            time_multiplier = None
            ei_multiplier = None

    return {
        "b_plus_f": b_plus_f,
        "d_max": d_max,
        "working_range": working_range,
        "target_range": target_range,
        "verdict": verdict,
        "shortfall": shortfall,
        "max_step_error": max_step_err,
        "time_multiplier": time_multiplier,
        "ei_multiplier": ei_multiplier,
        "speed_point_pixel": speed_pixel,
        "top_slope": top_slope,
        "mid_slope": mid_slope,
    }


def _target_pairs(px_values, b_plus_f, target_range):
    """Yield (pixel, target_density) over the measured pixel set.

    `target_range` is the paper's working range (speed-point to
    highlight); total density swing from pixel 0 to 255 is
    target_range + SPEED_POINT_OFFSET so the speed point lands near
    pixel = 255 * 0.10 / (target_range + 0.10)."""
    total = target_range + SPEED_POINT_OFFSET
    for px in px_values:
        yield px, b_plus_f + (px / 255.0) * total


def _inverse_at(densities, pixels, target_d):
    """Return the input pixel value where the measured curve crosses
    `target_d`, by linear interpolation between adjacent measurements.
    Returns None if `target_d` is outside the measured range."""
    n = len(densities)
    if target_d <= densities[0]:
        return float(pixels[0])
    if target_d >= densities[-1]:
        return float(pixels[-1])
    for i in range(n - 1):
        if densities[i] <= target_d <= densities[i + 1]:
            span = densities[i + 1] - densities[i]
            if span <= 0:
                return float(pixels[i])
            t = (target_d - densities[i]) / span
            return pixels[i] + t * (pixels[i + 1] - pixels[i])
    return None


# ---------------------------------------------------------------------------
# Empirical LUT inversion
# ---------------------------------------------------------------------------

def correct_lut(old_lut_display, measurements,
                target_range=PAPER_GRADE_RANGE[2]):
    """Compute a corrected 256-entry LUT (display space).

    Args:
        old_lut_display: the current Master A as a 256-tuple of u32
            display values (stored * scale_r).
        measurements: sequence of (pixel_value, density) in increasing
            pixel order.
        target_range: paper's log-density working range from speed point
            (b+f + 0.10) to highlight (default grade 2 = 1.05).

    The corrected LUT satisfies:
        for each input X, NEW_LUT[X] = OLD_LUT[Y_inv(D_target(X))]
    where D_target maps pixel 0 -> b+f and pixel 255 -> b+f + 0.10 +
    target_range, linearly in pixel value, so the working range from
    speed point to highlight equals `target_range`.  Y_inv inverts the
    measured pixel-to-density response.
    """
    if len(old_lut_display) != 256:
        raise ValueError("old_lut_display must be 256 entries")
    px = [m[0] for m in measurements]
    dens = [m[1] for m in measurements]
    if px != sorted(px):
        raise ValueError("measurements must be in increasing pixel order")

    b_plus_f = min(dens)
    total_range = target_range + SPEED_POINT_OFFSET

    # Build the new LUT.
    new_lut = [0] * 256
    for x in range(256):
        target_d = b_plus_f + (x / 255.0) * total_range
        y_inv = _inverse_at(dens, px, target_d)
        if y_inv is None:
            # Target outside measured range -- clip to nearest edge.
            y_inv = px[-1] if target_d > dens[-1] else px[0]
        y_clamped = max(0.0, min(255.0, y_inv))
        # Sample OLD LUT at fractional index via linear interpolation.
        lo = int(math.floor(y_clamped))
        hi = min(lo + 1, 255)
        frac = y_clamped - lo
        new_lut[x] = round(
            old_lut_display[lo] * (1 - frac) + old_lut_display[hi] * frac
        )

    return _enforce_monotonic(new_lut)


def _enforce_monotonic(values):
    """Make the sequence non-decreasing in place; tiny adjustments only."""
    out = list(values)
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return tuple(out)


# ---------------------------------------------------------------------------
# Building a new FilmTable
# ---------------------------------------------------------------------------

def calibrated_master_a_display(source_table):
    """Recover the source FLM's Master A as a 256-entry display-space tuple."""
    s = source_table.lut_sets[7]
    return tuple(v * s.scale_r for v in s.red.values)


def calibrated_master_b_display(source_table):
    s = source_table.lut_sets[9]
    return tuple(v * s.scale_r for v in s.red.values)


def build_calibrated_table(source_table, new_master_a_display,
                           new_internal_name, new_name=None,
                           new_master_b_display=None):
    """Construct a new FilmTable from a corrected Master A (and optionally
    a corrected Master B).

    If `new_master_b_display` is None, Master B is derived as 0.5 *
    Master A in display space (matches the wizard convention).  Pass an
    explicit array when the 8K wedge has been measured separately.
    Per-set headers and raw_extended come from wizard_baselines helpers
    so the file remains CFR-compatible.
    """
    peak = max(new_master_a_display) or 1
    base = max(1, math.ceil(peak / wizard_baselines.U16_MAX))
    # CFR keeps Master A stored peak around 16-26K with the base scale.
    # Use the wizard's base-from-ISO heuristic if we can recover the ISO,
    # but in practice the simple ceil(peak/U16) works.
    if new_master_b_display is None:
        new_master_b_display = tuple(v // 2 for v in new_master_a_display)
    else:
        new_master_b_display = tuple(new_master_b_display)

    def encode(disp): return tuple(
        min(wizard_baselines.U16_MAX, round(v / base)) for v in disp
    )

    stored_a = encode(new_master_a_display)
    stored_b = encode(new_master_b_display)
    half_a = tuple((v + 1) >> 1 for v in stored_a)
    double_b = tuple(min(wizard_baselines.U16_MAX, v * 2) for v in stored_b)

    per_set_stored = (
        stored_a, half_a, stored_a, half_a, stored_a, half_a,
        stored_a, stored_a, double_b, stored_b,
    )

    sets = []
    for i in range(10):
        scale = wizard_baselines._SET0_R_SCALE_CONSTANT if i == 0 else \
            base + wizard_baselines._R_SCALE_OFFSET_PER_SET[i]
        ch = pp8k.LutChannel(values=per_set_stored[i])
        sets.append(pp8k.LutSet(
            red=ch, green=ch, blue=ch,
            scale_r=scale, scale_g=base, scale_b=base,
            header=wizard_baselines.lut_set_header_for(i, base),
        ))

    raw_ext = wizard_baselines._build_raw_extended(base)

    name = (new_name or source_table.name)[:24]
    new_table = pp8k.FilmTable(
        name=name,
        internal_name=new_internal_name,
        camera_type=source_table.camera_type,
        camera_type_name=source_table.camera_type_name,
        is_bw=source_table.is_bw,
        bw_filter=source_table.bw_filter,
        bw_filter_name=source_table.bw_filter_name,
        aspect_w=source_table.aspect_w,
        aspect_h=source_table.aspect_h,
        lut_sets=tuple(sets),
        encrypted_data=b"",
        flags=source_table.flags,
        raw_extended=raw_ext,
    )
    return pp8k.normalize_masters(new_table)


# ---------------------------------------------------------------------------
# Internal-name versioning (FP100V3 -> FP100V4 etc.)
# ---------------------------------------------------------------------------

# Calibration roll layout: one identification frame + 16 4K wedge frames
# + 16 8K wedge frames = 33 total, fits in a 35mm roll's ~36 exposures.
# Each wedge frame has two half-frame patches (steps 2N-1 and 2N), so
# 16 frames cover 31 patches with the last frame's right half showing
# an "END" marker.
_FRAMES_PER_RESOLUTION = 16


def _frame_step_pair(frame_idx_1_based):
    """For a wedge frame index 1..16, return the (left_step, right_step)
    1-indexed step numbers it carries.  Step 32 = None (END marker)."""
    left = 2 * frame_idx_1_based - 1
    right = 2 * frame_idx_1_based
    if right > WEDGE_STEP_COUNT:
        right = None
    return left, right


def create_calibration_roll(rolls_store, film_tables, profile_id):
    """Create a calibration roll for the given film-table profile.

    Snapshots the FLM, creates a roll marked `calibration_for=profile_id`,
    and populates it with:

    - 1 identification frame at 4K (dynamic; film name + date legend)
    - 16 4K wedge frames (static assets; 2 patches each, 31 patches total)
    - 16 8K wedge frames (static assets; same pattern but at 8K)

    The wedge frames come from `static/calibration/wedge/...`, sidestepping
    the per-frame renderer entirely -- those PNGs are pre-baked output
    images at the exact frame dimensions the firmware expects.
    """
    profile = film_tables.profile(profile_id)
    if profile is None:
        raise KeyError(profile_id)
    flm_bytes = film_tables.read_bytes(profile_id)
    if flm_bytes is None:
        raise FileNotFoundError(f"profile {profile_id} missing FLM bytes")
    bw_filter = profile.get("bw_filter")
    if profile.get("is_bw") and bw_filter not in (1, 2, 3):
        raise ValueError(
            "calibration requires a B&W FLM with filter 1/2/3 (not Clear)"
        )
    # v1: 35mm 3:2 only (asset sets we currently ship).
    if (profile.get("aspect_w"), profile.get("aspect_h")) != (3, 2):
        raise ValueError("v1 calibration is 35mm 3:2 only")

    roll_name = f"Calibration: {profile.get('name', profile_id)}"
    roll = rolls_store.create(
        roll_name, profile, flm_bytes,
        bw_filter=bw_filter, calibration_for=profile_id,
    )

    # 1. Identification frame (dynamic).
    id_bytes = _render_identification_image(profile)
    id_frame = rolls_store.add_image(
        roll["id"], id_bytes, "00_identification.png",
    )
    # The ID frame defaults to 4K (renderer chose based on source size,
    # which is fine for our purposes -- a tiny dynamic PNG).

    # 2 + 3. 4K and 8K wedge frames (static assets).
    static_root = _static_wedge_root()
    for resolution in ("4k", "8k"):
        for frame_n in range(1, _FRAMES_PER_RESOLUTION + 1):
            png_path = static_root / f"35mm-3x2-{resolution}" / f"frame_{frame_n:02d}.png"
            if not png_path.is_file():
                raise FileNotFoundError(f"missing wedge asset: {png_path}")
            _attach_prerendered_frame(
                rolls_store, roll["id"], png_path,
                resolution=resolution,
                original_name=f"{resolution}_frame_{frame_n:02d}.png",
            )

    return rolls_store.get(roll["id"])


def _static_wedge_root():
    """Resolve the on-disk path to the static wedge assets."""
    from pathlib import Path
    # pipalette package -> repo root -> static/calibration/wedge
    return Path(__file__).resolve().parent.parent / "static" / "calibration" / "wedge"


def _attach_prerendered_frame(rolls_store, roll_id, png_path,
                              resolution, original_name):
    """Add a frame using a pre-baked output PNG -- skips the per-frame
    renderer entirely.

    Writes:
        outputs/<frame_id>.png   = the pre-rendered wedge image
        thumbs/<frame_id>.jpg    = a 240px thumbnail
        images/<image_id>.png    = a tiny source (the wedge image itself)
    and appends the frame entry to the roll index with status=pending.
    """
    import hashlib
    import io
    import time
    import uuid
    from PIL import Image

    raw = png_path.read_bytes()
    image_id = hashlib.sha1(raw).hexdigest()[:16] + "_" + uuid.uuid4().hex[:6]
    frame_id = uuid.uuid4().hex[:12]

    roll_dir = rolls_store.roll_dir(roll_id)
    src_path = roll_dir / "images" / (image_id + ".png")
    out_path = roll_dir / "outputs" / (frame_id + ".png")
    thumb_path = roll_dir / "thumbs" / (frame_id + ".jpg")

    src_path.write_bytes(raw)
    out_path.write_bytes(raw)  # pre-rendered: source == output

    # Cheap 240px thumbnail of the pre-rendered output.
    with Image.open(io.BytesIO(raw)) as img:
        img.thumbnail((240, 240))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(thumb_path, "JPEG", quality=80)

    with rolls_store._lock:
        roll = rolls_store._find(roll_id)
        if roll is None:
            raise KeyError(roll_id)
        with Image.open(io.BytesIO(raw)) as probe:
            src_w, src_h = probe.size
        roll["frames"].append({
            "id": frame_id,
            "image_id": image_id,
            "image_filename": image_id + ".png",
            "original_name": original_name,
            "src_width": src_w,
            "src_height": src_h,
            "resolution": resolution,
            "transform": "fit",
            "rotation": 0,
            "background": "black",
            "order": len(roll["frames"]),
            "status": "pending",
            "exposure_count": 0,
            "exposed_at": None,
            "last_error": None,
            "transform_warning": None,
        })
        rolls_store._save()
    return rolls_store._find(roll_id)["frames"][-1]


def _render_identification_image(profile, width=4096, height=2731):
    """Return PNG bytes of a black-background identification frame with
    the film name, internal name, current date, and roll legend."""
    import time
    from PIL import Image, ImageDraw, ImageFont
    import io
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    # Force the BASIC layout engine.  Pillow's default RAQM layout
    # (via HarfBuzz) shares global FreeType state with pyvips and ends
    # up rendering text at ~1000x intended size when pyvips is loaded
    # in the same process -- which is always the case in the Flask app.
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 140,
            layout_engine=ImageFont.Layout.BASIC,
        )
    except Exception:
        font = ImageFont.load_default()
    lines = [
        "piPalette CALIBRATION ROLL",
        "",
        f"Film:      {profile.get('name', '(unnamed)')}",
        f"Internal:  {profile.get('id')}",
        f"Filter:    {profile.get('bw_filter_name', '?')}",
        f"Date:      {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "4K WEDGE: frames 02..17  (Master A, 31 steps)",
        "8K WEDGE: frames 18..33  (Master B, 31 steps)",
        "",
        "Two patches per frame.  Labels show step, res, pixel value.",
    ]
    y = 200
    for line in lines:
        draw.text((200, y), line, fill=255, font=font)
        y += 200
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def next_versioned_name(source_name, existing_names):
    """Increment a trailing version number in `source_name`.

    Examples: 'FP100V3' -> 'FP100V4', 'FOMA' -> 'FOMA-2'.  Falls back to
    appending '-2', '-3', ... if the source has no obvious version
    suffix.  Always returns a name <= 8 chars and not in existing_names.
    """
    import re
    taken = set(existing_names)

    # Try to find trailing digits and increment them.
    m = re.search(r"(\d+)$", source_name)
    if m:
        prefix = source_name[:m.start()]
        n = int(m.group(1)) + 1
        while True:
            candidate = f"{prefix}{n}"[:8]
            if candidate not in taken and candidate != source_name:
                return candidate
            n += 1

    # No trailing digits -- append -2, -3, ...
    stem = source_name[:6]
    n = 2
    while True:
        candidate = f"{stem}-{n}"[:8]
        if candidate not in taken:
            return candidate
        n += 1
