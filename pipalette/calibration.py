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

import numpy as np
import pp8k

from . import calibration_shape, wizard_baselines


# Grade-2 paper handles ~1.05 log density (ISO range).  Other grades:
# 0 -> 1.60, 1 -> 1.40, 2 -> 1.20-1.05, 3 -> 1.05-0.95, 4 -> 0.85, 5 -> 0.70.
# We use 1.05 as the conservative "grade 2 hard" anchor.
PAPER_GRADE_RANGE = {0: 1.60, 1: 1.40, 2: 1.05, 3: 0.95, 4: 0.85, 5: 0.70}

# Speed-point offset above b+f (ISO 6:1993 standard).
SPEED_POINT_OFFSET = 0.10

# Pixel value at which Zone I lands.  256 input levels divide cleanly
# into ~9 photographic zones (Zone 0 paper-black to Zone IX paper-white);
# Zone I sits one zone-width above Zone 0 at pixel ~25.  The calibrated
# LUT anchors the speed point exactly here, with pixels 0..25 forming
# the toe (Zone 0, paper-black).
ZONE_I_PIXEL = 25

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

    # Per-step error vs target curve.  Target uses the same two-region
    # shape that correct_lut produces (toe up to ZONE_I_PIXEL, working
    # range above).  Detects shape mismatches even when the overall
    # range is on target.
    max_step_err = 0.0
    for px_val, d_meas in measurements:
        target_d = next(_target_pairs([px_val], b_plus_f, target_range))[1]
        err = abs(d_meas - target_d)
        if err > max_step_err:
            max_step_err = err

    # Verdict selection.  Two cases that matter photographically:
    #
    # - 'shouldered': curve has rolled off at the top, film + developer
    #   can't deliver the full working range no matter what the LUT
    #   does.  Recommend longer development (time_multiplier) or harder
    #   paper grade.
    # - 'ok' / 'lut_fixable': all corrections are LUT-shape work that
    #   the calibration apply step handles.  We don't report an "EI
    #   shift" -- the LUT amplitude IS the exposure control in our
    #   system, and the corrected LUT bakes that adjustment in.
    if shouldered and shortfall >= 0.03:
        verdict = "shouldered"
        time_multiplier = (target_range / working_range
                          if working_range > 0 else None)
    elif shortfall < 0.03 and max_step_err < 0.05:
        verdict = "ok"
        time_multiplier = None
    else:
        verdict = "lut_fixable"
        time_multiplier = None

    return {
        "b_plus_f": b_plus_f,
        "d_max": d_max,
        "working_range": working_range,
        "target_range": target_range,
        "verdict": verdict,
        "shortfall": shortfall,
        "max_step_error": max_step_err,
        "time_multiplier": time_multiplier,
        "speed_point_pixel": speed_pixel,
        "top_slope": top_slope,
        "mid_slope": mid_slope,
    }


def _target_pairs(px_values, b_plus_f, target_range):
    """Yield (pixel, target_density) over the measured pixel set.

    Two-region target: pixels 0..ZONE_I_PIXEL form the toe (densities
    from b+f up to the speed point); pixels ZONE_I_PIXEL..255 are the
    working range, linear in density to the highlight."""
    sp_density = b_plus_f + SPEED_POINT_OFFSET
    for px in px_values:
        if px <= ZONE_I_PIXEL:
            yield px, b_plus_f + (px / ZONE_I_PIXEL) * SPEED_POINT_OFFSET
        else:
            t = (px - ZONE_I_PIXEL) / (255 - ZONE_I_PIXEL)
            yield px, sp_density + t * target_range


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
# Smooth film-response model (cubic in log-drive)
# ---------------------------------------------------------------------------
# Photographic films respond via the Hurter-Driffield curve (density vs
# log exposure) with three smooth regions: toe, straight section,
# shoulder.  CRT phosphor output is also smooth in drive level.  The
# composition is therefore a smooth function in the working range with
# at most ~3 degrees of freedom -- there should be NO kinks or jumps.
#
# Calibration via piecewise interpolation (PCHIP through every measured
# point) faithfully tracks ±0.02 densitometer noise as visible kinks
# in the LUT.  Fitting a low-degree polynomial through all measurements
# via least squares is the correct approach: physics guarantees the
# answer is smooth, and over-determined samples (31 measurements, 4
# parameters) let LSF average down the noise.
#
# Model:  density = polynomial(L)  degree 4
# where   L = log(drive + 1)
# A degree-4 polynomial in log-drive captures the H&D shape (toe,
# straight section, shoulder) without overshooting between knots.
# The inverse is monotone within the working range and we evaluate it
# by bisection on the smooth fit.

def _running_max(values):
    """Clamp to non-decreasing by raising each entry to the prior max."""
    out = []
    m = values[0]
    for v in values:
        if v > m:
            m = v
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Speed-point calibration (first-time, single-measurement)
# ---------------------------------------------------------------------------
# Refinement assumes the LUT is already close.  For a brand-new
# film/dev/process combo we don't know that, and the 31-step wedge
# through an arbitrary starting LUT may have no useful samples around
# the actual speed point.  Speed-point calibration solves this by:
#
#   1. Exposing the wedge through a KNOWN linear LUT (calibration_lut)
#      so drives are pre-determined, log-spaced, and independent of
#      whatever curve the wizard placed.
#   2. Measuring density at 24 log-spaced drives that span ±2 stops
#      around the predicted speed point for the labeled ISO.
#   3. Interpolating to find the drive at which density crosses
#      b+f + 0.10 -- that drive IS the speed point on this film+dev.
#   4. Placing a reference H&D shape (see calibration_shape) anchored
#      at the measured speed-point drive, so the output LUT has a
#      film-physics-plausible curve even though we only measured one
#      point.
#
# After that, refinement (correct_lut) can fine-tune the curve shape
# from a 31-step wedge through the now-close LUT.


def find_speed_point(measurements, sp_offset=SPEED_POINT_OFFSET):
    """Find the drive level where density crosses b+f + sp_offset.

    Args:
        measurements: sequence of (drive, density) pairs.  Drive values
            are absolute display-space drives.  A measurement at drive=0
            is accepted and used as the b+f anchor (it doesn't enter the
            log-space interpolation since log(0) is undefined).
        sp_offset: density above b+f that defines the speed point
            (ISO 6:1993 -> 0.10).

    Returns: speed-point drive (float).
    Raises ValueError if the wedge doesn't bracket the speed point.
    """
    if len(measurements) < 4:
        raise ValueError("need at least 4 measurements")
    sorted_meas = sorted((float(m[0]), float(m[1])) for m in measurements)
    if sorted_meas[0][0] < 0:
        raise ValueError("drives must be >= 0")

    # Separate the drive=0 anchor (if present) -- it gives us b+f but
    # can't take part in log-drive interpolation.
    if sorted_meas[0][0] == 0:
        b_plus_f = sorted_meas[0][1]
        positive = sorted_meas[1:]
    else:
        b_plus_f = min(d for _, d in sorted_meas)
        positive = sorted_meas
    if len(positive) < 3:
        raise ValueError("need at least 3 positive-drive measurements")

    target = b_plus_f + sp_offset
    drives = [m[0] for m in positive]
    densities = [m[1] for m in positive]
    # Running-max smoothing so a noisy reading doesn't push the speed
    # point past where the film actually crossed it.
    smooth = _running_max(densities)

    if target < smooth[0]:
        raise ValueError(
            f"speed-point density {target:.3f} below lowest positive-drive "
            f"measurement {smooth[0]:.3f} at drive {drives[0]:.0f} -- film "
            f"is faster than the wedge covers; try a higher labeled ISO"
        )
    if target > smooth[-1]:
        raise ValueError(
            f"speed-point density {target:.3f} above highest measured "
            f"{smooth[-1]:.3f} -- wedge tops out too low (drives too low) "
            f"or film responds very slowly"
        )

    # Linear interpolation in log-drive space, between the bracket
    # whose densities straddle the target.
    for i in range(len(smooth) - 1):
        if smooth[i] <= target <= smooth[i + 1]:
            d0, d1 = smooth[i], smooth[i + 1]
            l0, l1 = math.log(drives[i]), math.log(drives[i + 1])
            if d1 == d0:
                return drives[i]
            t = (target - d0) / (d1 - d0)
            return math.exp(l0 + t * (l1 - l0))
    raise AssertionError("unreachable: target bracketed but not found")


def build_speedpoint_lut(D_sp_4k, D_sp_8k,
                         target_range=PAPER_GRADE_RANGE[2]):
    """Build (Master A display, Master B display) from measured speed points.

    Args:
        D_sp_4k, D_sp_8k: measured speed-point drives at each resolution.
        target_range: density range above speed point to span across
            pixels ZONE_I_PIXEL+1 .. 255 (default grade-2 paper = 1.05).

    Returns (master_a_display, master_b_display) -- two 256-tuples
    suitable for build_calibrated_table().  Both follow the reference
    H&D shape anchored at their respective speed-point drives.
    """
    master_a = calibration_shape.place_shape(
        D_sp_4k, target_range, zone_i_pixel=ZONE_I_PIXEL)
    master_b = calibration_shape.place_shape(
        D_sp_8k, target_range, zone_i_pixel=ZONE_I_PIXEL)
    return master_a, master_b


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
    px = [float(m[0]) for m in measurements]
    raw_dens = [float(m[1]) for m in measurements]
    if px != sorted(px):
        raise ValueError("measurements must be in increasing pixel order")

    # Recover the film response curve.  At each wedge measurement X_i,
    # the drive level was OLD_LUT[X_i] and the resulting density was
    # raw_dens[i], giving 31 (drive, density) samples of the film's
    # response f(drive) -> density.  Smooth the density readings with
    # a 3-point moving average before forcing monotonicity, so a single
    # noisy reading doesn't bias the fit.
    drives = np.array([_sample_old_lut(old_lut_display, p) for p in px])
    smoothed = _moving_average(raw_dens, window=3)
    dens = np.array(_running_max(smoothed))

    # Degree-3 polynomial fit in log(drive+1) space.  Physics guarantees
    # the response is smooth -- piecewise interpolation would track
    # densitometer noise as visible LUT kinks.  Degree 3 is plenty for
    # the H&D shape (toe / straight / shoulder) and unlike degree 4 it
    # is reliably monotone within the working range.
    L = np.log(drives + 1.0)
    coeffs = np.polyfit(L, dens, deg=3)
    fit = np.poly1d(coeffs)

    b_plus_f = float(dens.min())
    sp_density = b_plus_f + SPEED_POINT_OFFSET
    highlight_density = sp_density + target_range
    L_min, L_max = float(L.min()), float(L.max())

    # Two-region LUT:
    #
    # 1. Pixels 0..ZONE_I_PIXEL: smooth toe ramp from drive 0 to the
    #    drive that produces the speed point.  These pixels land at or
    #    below b+f + 0.10 -- on grade-2 paper they all print as Zone 0
    #    (paper-black), so the exact drive shape here is cosmetic.  A
    #    linear ramp gives the LUT a clean visual rise instead of a
    #    cluster of zeros, but doesn't change what the print sees.
    #
    # 2. Pixels ZONE_I_PIXEL..255: working range.  Target density is
    #    linear in pixel value from b+f + 0.10 (Zone I, paper-visible
    #    detail) to b+f + 0.10 + target_range (Zone IX, paper-white).
    #    Invert the polynomial fit at each target.
    log_drive_speed = _invert_poly(fit, sp_density, L_min, L_max)
    drive_speed = max(0.0, math.exp(log_drive_speed) - 1.0)

    new_lut = [0] * 256
    for x in range(ZONE_I_PIXEL + 1):
        new_lut[x] = round((x / ZONE_I_PIXEL) * drive_speed)
    for x in range(ZONE_I_PIXEL + 1, 256):
        t = (x - ZONE_I_PIXEL) / (255 - ZONE_I_PIXEL)
        target_d = sp_density + t * target_range
        log_drive = _invert_poly(fit, target_d, L_min, L_max)
        drive = math.exp(log_drive) - 1.0
        new_lut[x] = max(0, round(drive))

    return _enforce_monotonic(new_lut)


def _moving_average(values, window=3):
    """Symmetric moving-average smoother (shrinking window at endpoints)."""
    n = len(values)
    half = window // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _sample_old_lut(old_lut, pixel_index):
    """Linearly-interpolate OLD_LUT at a (possibly fractional) pixel index."""
    if pixel_index <= 0:
        return float(old_lut[0])
    if pixel_index >= 255:
        return float(old_lut[255])
    lo = int(math.floor(pixel_index))
    frac = pixel_index - lo
    return old_lut[lo] * (1 - frac) + old_lut[lo + 1] * frac


def _invert_poly(fit, target_y, x_lo, x_hi, iters=50):
    """Find x in [x_lo, x_hi] with fit(x) == target_y via bisection.

    `fit` is monotone non-decreasing within the working range (cubic
    polynomial fit to a Hurter-Driffield-shaped response, which can't
    invert in the toe/straight/shoulder zones photography lives in)."""
    y_lo, y_hi = fit(x_lo), fit(x_hi)
    if target_y <= y_lo: return x_lo
    if target_y >= y_hi: return x_hi
    for _ in range(iters):
        mid = 0.5 * (x_lo + x_hi)
        if fit(mid) < target_y:
            x_lo = mid
        else:
            x_hi = mid
    return 0.5 * (x_lo + x_hi)


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
# + 16 8K wedge frames + 2 print-side reference frames = 35 total, fits
# in a 35mm roll's ~36 exposures.  Each wedge frame has two half-frame
# patches (steps 2N-1 and 2N), so 16 frames cover 31 patches with the
# last frame's left half showing an "END" marker (right is step 31).
# The reference frames are full-frame 11-step ramps (LINEAR and sRGB)
# for visual print-side evaluation -- no densitometer measurement.
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

    roll_name = f"Refinement: {profile.get('name', profile_id)}"
    roll = rolls_store.create(
        roll_name, profile, flm_bytes,
        bw_filter=bw_filter, calibration_for=profile_id,
        calibration_mode="refinement",
    )

    # 1. Identification frame (dynamic).  Generated at 4K and attached
    #    via the same prerendered path that the wedge frames use, so we
    #    also skip the renderer + thumb generation for it.
    id_bytes = _render_identification_image(profile)
    _attach_prerendered_frame(
        rolls_store, roll["id"], id_bytes,
        resolution="4k",
        original_name="00_identification.png",
        src_dimensions=(4096, 2731),
    )

    # 2 + 3. 4K and 8K wedge frames (static assets).  Cache dimensions
    #    once per resolution -- they don't vary across the 16 frames.
    static_root = _static_wedge_root()
    cached_dims = {}
    for resolution in ("4k", "8k"):
        for frame_n in range(1, _FRAMES_PER_RESOLUTION + 1):
            png_path = static_root / f"35mm-3x2-{resolution}" / f"frame_{frame_n:02d}.png"
            if not png_path.is_file():
                raise FileNotFoundError(f"missing wedge asset: {png_path}")
            raw = png_path.read_bytes()
            if resolution not in cached_dims:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as probe:
                    cached_dims[resolution] = probe.size
            _attach_prerendered_frame(
                rolls_store, roll["id"], raw,
                resolution=resolution,
                original_name=f"{resolution}_frame_{frame_n:02d}.png",
                src_dimensions=cached_dims[resolution],
            )

    # 4. Print-side reference frames (4K static assets).  Not measured by
    #    the densitometer step -- these print on the final paper and the
    #    user evaluates by eye whether the chain reproduces equal
    #    linear-light steps and/or equal sRGB-pixel-value steps faithfully.
    reference_root = _static_reference_root()
    for slug in ("linear", "srgb"):
        png_path = reference_root / f"{slug}_35mm-3x2-4k.png"
        if not png_path.is_file():
            raise FileNotFoundError(f"missing reference asset: {png_path}")
        raw = png_path.read_bytes()
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as probe:
            dims = probe.size
        _attach_prerendered_frame(
            rolls_store, roll["id"], raw,
            resolution="4k",
            original_name=f"reference_{slug}_4k.png",
            src_dimensions=dims,
        )

    return rolls_store.get(roll["id"])


def create_speedpoint_roll(rolls_store, film_tables, profile_id):
    """Create a SPEED-POINT calibration roll for the given profile.

    Differs from refinement (create_calibration_roll) in two ways:

    1. The roll is exposed through a per-ISO calibration LUT (built
       from calibration_lut), not the user's target FLM.  The
       calibration LUT encodes 24 log-spaced drives at pixels 1..24,
       so the user gets clean ±2-stop coverage around the predicted
       speed point regardless of how off the target FLM's curve is.
    2. The wedge frames are rendered ON DEMAND (wedge_render) with
       per-patch drive labels baked in, so the densitometer entry
       grid doesn't need any per-roll metadata file -- the labels
       on the developed film are self-describing.

    Roll layout: 1 ID + 12 4K wedge + 12 8K wedge + 2 reference frames
    = 27 frames (fits in a 36-exposure roll comfortably).
    """
    from . import calibration_lut, wedge_render
    profile = film_tables.profile(profile_id)
    if profile is None:
        raise KeyError(profile_id)
    bw_filter = profile.get("bw_filter")
    if profile.get("is_bw") and bw_filter not in (1, 2, 3):
        raise ValueError(
            "speed-point calibration requires a B&W FLM with filter 1/2/3"
        )
    if (profile.get("aspect_w"), profile.get("aspect_h")) != (3, 2):
        raise ValueError("speed-point calibration is 35mm 3:2 only")
    iso = profile.get("iso")
    if iso is None:
        raise ValueError(
            "profile is missing iso -- can't predict speed-point drive"
        )

    template = film_tables.read_table(profile_id)
    if template is None:
        raise FileNotFoundError(f"profile {profile_id} missing FLM bytes")

    # Build the per-ISO calibration LUT FLM that becomes the roll's
    # exposure profile.  Pixel i (i=1..24) -> log-spaced drive i.
    cal_flm_bytes = calibration_lut.build_calibration_flm_bytes(template, iso)

    roll_name = f"Speed-point: {profile.get('name', profile_id)}"
    roll = rolls_store.create(
        roll_name, profile, cal_flm_bytes,
        bw_filter=bw_filter, calibration_for=profile_id,
        calibration_mode="speed_point",
    )

    # 1. Identification frame -- subtitled for the speed-point flow.
    K_4k = calibration_lut.scale_for(iso, "4k")
    K_8k = calibration_lut.scale_for(iso, "8k")
    id_bytes = _render_identification_image(
        profile,
        mode_label="SPEED-POINT CALIBRATION",
        wedge_legend=[
            f"4K WEDGE:    frames 02..13  (24 patches, linear ramp 0..{K_4k*255})",
            f"8K WEDGE:    frames 14..25  (24 patches, linear ramp 0..{K_8k*255})",
            "REFERENCES:  frames 26..27  (LINEAR + sRGB print test ramps)",
        ],
    )
    _attach_prerendered_frame(
        rolls_store, roll["id"], id_bytes,
        resolution="4k",
        original_name="00_identification.png",
        src_dimensions=(4096, 2731),
    )

    # 2 + 3. 4K and 8K wedge frames, rendered on demand from the
    #        wedge_render module.  Each frame carries two patches with
    #        per-patch drive labels.
    for resolution in ("4k", "8k"):
        cached_dims = None
        frame_n = 0
        for left, right in wedge_render.frame_pairs():
            frame_n += 1
            raw = wedge_render.render_frame(iso, resolution, left, right)
            if cached_dims is None:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as probe:
                    cached_dims = probe.size
            _attach_prerendered_frame(
                rolls_store, roll["id"], raw,
                resolution=resolution,
                original_name=f"{resolution}_speedpoint_{frame_n:02d}.png",
                src_dimensions=cached_dims,
            )

    # 4. Print-side reference frames (4K static assets).  Same set the
    #    refinement roll attaches -- they describe screen-to-print
    #    fidelity, which is orthogonal to the wedge type.
    reference_root = _static_reference_root()
    for slug in ("linear", "srgb"):
        png_path = reference_root / f"{slug}_35mm-3x2-4k.png"
        if not png_path.is_file():
            raise FileNotFoundError(f"missing reference asset: {png_path}")
        raw = png_path.read_bytes()
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as probe:
            dims = probe.size
        _attach_prerendered_frame(
            rolls_store, roll["id"], raw,
            resolution="4k",
            original_name=f"reference_{slug}_4k.png",
            src_dimensions=dims,
        )

    return rolls_store.get(roll["id"])


def _static_wedge_root():
    """Resolve the on-disk path to the static wedge assets."""
    from pathlib import Path
    # pipalette package -> repo root -> static/calibration/wedge
    return Path(__file__).resolve().parent.parent / "static" / "calibration" / "wedge"


def _static_reference_root():
    """Resolve the on-disk path to the print-side reference assets."""
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "static" / "calibration" / "reference"


def _attach_prerendered_frame(rolls_store, roll_id, raw_png_bytes,
                              resolution, original_name,
                              src_dimensions=None):
    """Add a frame using pre-baked output PNG bytes -- skips the
    per-frame renderer AND skips thumbnail generation.

    Calibration rolls are hidden from /rolls and the FLM-page UI doesn't
    show per-frame previews, so the thumbnail step (which on the Pi runs
    32x at PIL.Image.thumbnail on a 4K/8K PNG and takes most of the roll-
    creation wall-clock) is pure waste here.

    Writes:
        outputs/<frame_id>.png   = the pre-rendered output image
        images/<image_id>.png    = the same bytes (kept for schema parity)
    and appends a frame entry with status=pending.  No thumb file.
    """
    import hashlib
    import io
    import uuid
    from PIL import Image

    raw = raw_png_bytes
    image_id = hashlib.sha1(raw).hexdigest()[:16] + "_" + uuid.uuid4().hex[:6]
    frame_id = uuid.uuid4().hex[:12]

    roll_dir = rolls_store.roll_dir(roll_id)
    src_path = roll_dir / "images" / (image_id + ".png")
    out_path = roll_dir / "outputs" / (frame_id + ".png")

    src_path.write_bytes(raw)
    out_path.write_bytes(raw)

    if src_dimensions is None:
        with Image.open(io.BytesIO(raw)) as probe:
            src_dimensions = probe.size

    with rolls_store._lock:
        roll = rolls_store._find(roll_id)
        if roll is None:
            raise KeyError(roll_id)
        src_w, src_h = src_dimensions
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


def _render_identification_image(profile, width=4096, height=2731,
                                 mode_label="REFINEMENT CALIBRATION",
                                 wedge_legend=None):
    """Return PNG bytes of a black-background identification frame with
    the film name, internal name, current date, and roll legend.

    mode_label and wedge_legend let the speed-point flow override the
    title and frame-range lines without duplicating this whole renderer.
    """
    import time
    from PIL import Image, ImageDraw, ImageFont
    import io
    if wedge_legend is None:
        wedge_legend = [
            "4K WEDGE:    frames 02..17  (Master A, 31 steps)",
            "8K WEDGE:    frames 18..33  (Master B, 31 steps)",
            "REFERENCES:  frames 34..35  (LINEAR + sRGB print test ramps)",
        ]
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
        f"piPalette {mode_label} ROLL",
        "",
        f"Film:      {profile.get('name', '(unnamed)')}",
        f"Internal:  {profile.get('id')}",
        f"Filter:    {profile.get('bw_filter_name', '?')}",
        f"Date:      {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        *wedge_legend,
        "",
        "Two patches per wedge frame.  Labels show step, res, pixel value.",
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
