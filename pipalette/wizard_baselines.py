"""Bundled placeholder baseline curves for the FLM creation wizard.

New FLMs created via the wizard ship with cal_state=uncalibrated and
the sigmoid placeholder LUT below.  The placeholder is film-agnostic
by construction: it's the reference H&D shape from calibration_shape
anchored at predicted ISO-100 speed-point drives (D_sp_4k=150,
D_sp_8k=38).  No specific film's data is encoded.

After the user runs "Find speed point" calibration (the first-time
flow), the FLM is replaced with a curve anchored at the MEASURED
speed point and the cal_state flips to "speed_point".  Refinement
calibration further fits the curve shape to the actual film response
once speed is known.

The wizard scales these arrays by 100/iso for other ISOs -- slower
films land at higher drives, faster films at lower.  Encoding through
the factory master-pattern (Sets 0/2/4/6/7 = Master A; Sets 1/3/5 =
ceil(Master A / 2); Set 8 = 2 * Master B; Set 9 = Master B) follows
the same per-set scale offsets verified across 60/62 Polaroid factory
FLMs: (0, +48, +32, +32, +16, +16, 0, 0, 0, 0) relative to Master A.

Earlier versions of this module shipped Foma-Pan-100-derived curves as
the baseline.  Those are kept as a reference in
docs/reference_curves/foma_pan_100.json -- they're useful as a sanity
check but no longer assumed to apply to every ISO-100 film.
"""

import math

import pp8k

from . import calibration_shape


# Predicted speed-point drives at ISO 100 (the reference).  Same values
# the per-ISO calibration LUT centres its wedge around (calibration_lut
# module).  At other wizard ISOs the placeholder scales by 100/iso so a
# regular roll exposed through an uncalibrated FLM lands speed point
# roughly at pixel 25 if the film is exactly its labeled ISO.
PLACEHOLDER_D_SP_4K = 150.0
PLACEHOLDER_D_SP_8K = 38.0
PLACEHOLDER_TARGET_RANGE = 1.05  # grade-2 paper

# Master A: 4K-resolution curve, loaded by firmware at HRES=4096.
# Sigmoid reference shape anchored at the predicted ISO-100 4K speed-point
# drive (=150).  See calibration_shape for the H&D model.
MASTER_A_DISPLAY = calibration_shape.place_shape(
    PLACEHOLDER_D_SP_4K, PLACEHOLDER_TARGET_RANGE)

# Master B: 8K-resolution curve, loaded by firmware at HRES=8192.
# Same shape, anchored at the predicted ISO-100 8K speed-point drive (=38).
MASTER_B_DISPLAY = calibration_shape.place_shape(
    PLACEHOLDER_D_SP_8K, PLACEHOLDER_TARGET_RANGE)

# The placeholder baselines above are anchored at ISO 100.  Scaling
# target is REF_ISO / target_iso (slower films need higher drives).
REF_ISO = 100

# Base scale per ISO -- picks the scale factor for Master A (Set 7).
# With the sigmoid placeholder (peak ~340 at ISO 100), base=1 fits the
# full ISO range comfortably: ISO 25 peak is ~1350, still well inside
# u16.  We keep the per-ISO map so post-calibration FLMs with much
# larger peaks can override these defaults if needed.
_BASE_BY_ISO = {25: 1, 50: 1, 100: 1, 200: 1, 400: 1, 800: 1}

# --------------------------------------------------------------------
# FLM format constants -- field-by-field, matching the spec in
# `pipalette_old/FILMTABLE_ANALYSIS.md` and `pp8k/docs/book/04-profiles.md`.
# --------------------------------------------------------------------

# HRES marker (file bytes 0-1 of each set's 10-byte per-set header).
# The firmware nearest-matches MODE SELECT HRES against these values
# to pick which LUT set to load.  Sets 7 and 9 are the production
# tiers (4K/8K).  CFR uses 2016 for the 2K tier; factory files use
# 2048.  We match CFR since this is the user's authoring tool.
_HRES_PER_SET = (
    None,    # Set 0 has no per-set header
    1024,    # Set 1: 1K
    1024,    # Set 2: 1K variant
    2016,    # Set 3: 2K (CFR convention; factory uses 2048)
    2016,    # Set 4: 2K variant
    4032,    # Set 5: ~4K legacy
    4032,    # Set 6: ~4K variant
    4096,    # Set 7: 4K production (Master A)
    4097,    # Set 8: 4K+
    8192,    # Set 9: 8K production (Master B)
)

# Per-set R-scale offset relative to base.  Empirically verified across
# 62 factory FLMs + 6 CFR references -- see project memory.  Lower-HRES
# sets get higher R-scales to compensate for fewer scanlines per frame.
# Set 0's effective R-scale is a separate constant; the SUB_MASTER values
# only describe Sets 1-9.
_R_SCALE_OFFSET_PER_SET = (
    None,    # Set 0 has no per-set header
    48,      # Set 1
    32,      # Set 2
    32,      # Set 3
    16,      # Set 4
    16,      # Set 5
    0,       # Set 6
    0,       # Set 7 (Master A)
    0,       # Set 8
    0,       # Set 9 (Master B)
)

# Set 0 has no per-set header; its R/G/B scales live in the file header.
# CFR fixes Set 0's R-scale at 2 regardless of base (a CFR-specific
# convention; the firmware doesn't depend on this for B&W with the Blue
# filter, since R isn't exposed).
_SET0_R_SCALE_CONSTANT = 2

# File bytes 28-31: prefix, identical across all 62 factory files.
_PREFIX = bytes([0x20, 0x20, 0x30, 0x00])  # "  0\0"

# File bytes 40-75 (36 bytes): tool/build metadata.  Not parsed by
# firmware.  CFR writes consistent values across its 6 ISO references;
# we copy them verbatim so our output is byte-equivalent to CFR for the
# fields CFR populates.
_METADATA_REGION = bytes.fromhex(
    "0064000000000000000000000000000000000000000035303000393939000a000000a001"
)
assert len(_METADATA_REGION) == 36, f"metadata is {len(_METADATA_REGION)}B"

# File bytes 76-107 (32 bytes): format constant, byte-identical across
# all 62 factory FLMs AND all 6 CFR references.  Some kind of format
# signature; firmware likely checks it for validity.
_FORMAT_CONSTANT = bytes.fromhex(
    "010101b601010101ca01010101dc01010101020201010121020101014c020101"
)
assert len(_FORMAT_CONSTANT) == 32, f"format_const is {len(_FORMAT_CONSTANT)}B"

# File bytes 108-177 (70 bytes): resolution-ladder metadata.  Byte-
# identical across all 62 factory files AND all 6 CFR references.
# Partially decoded (the analysis doc speculates "varies per film", but
# empirically it doesn't vary for any of our reference data).
_RESOLUTION_LADDER = bytes.fromhex(
    "017202010101a001010101b601010101ca01010101dc01010101020201010121020101014c020101017202010101010001000100010001000100020002000200020002000200"
)
assert len(_RESOLUTION_LADDER) == 70, f"res_ladder is {len(_RESOLUTION_LADDER)}B"

# File bytes 178-179 (2 bytes): "film class" marker that varies across
# factory files but is constant `0a00` across all CFR references.  Until
# we decode what it actually encodes, we use CFR's value.
_FILM_CLASS_MARKER = bytes.fromhex("0a00")


def _build_raw_extended(base):
    """Construct the 161-byte extended file header from labelled regions.

    Layout (file offsets; raw_extended starts at file byte 28):
        0-3   PREFIX               constant
        4-11  internal_name slot   pp8k serializer overwrites
        12-47 METADATA_REGION      tool/build metadata
        48-79 FORMAT_CONSTANT      format signature
        80-149 RESOLUTION_LADDER   resolution ladder values
        150-151 FILM_CLASS_MARKER  film class
        152   Set 0 R-scale        constant 2
        153   Set 1 R-scale        base + 48 (max R-scale in file)
        154   Set 0 G-scale        base
        155   Set 0 B-scale        base
        156   additional scale     base
        157-160 padding            [1,1,1,1]
    """
    buf = bytearray(161)
    buf[0:4] = _PREFIX
    # bytes 4-11: internal_name slot, filled by pp8k.serialize_flm
    buf[12:48] = _METADATA_REGION
    buf[48:80] = _FORMAT_CONSTANT
    buf[80:150] = _RESOLUTION_LADDER
    buf[150:152] = _FILM_CLASS_MARKER
    buf[152] = _SET0_R_SCALE_CONSTANT
    buf[153] = (base + _R_SCALE_OFFSET_PER_SET[1]) & 0xFF  # max R-scale = Set 1's
    buf[154] = base & 0xFF
    buf[155] = base & 0xFF
    buf[156] = base & 0xFF
    buf[157:161] = bytes([0x01, 0x01, 0x01, 0x01])
    return bytes(buf)


def _build_set_header(set_index, base):
    """Construct the 10-byte per-set header for sets 1-9.

    Set 0 has no header (returns None).  Bytes 0-1 are the HRES marker
    little-endian; bytes 2-4 are R/G/B scales; byte 5 is an additional
    base copy CFR writes; bytes 6-9 are [1,1,1,1] padding.
    """
    if set_index == 0:
        return None
    hres = _HRES_PER_SET[set_index]
    scale_r = (base + _R_SCALE_OFFSET_PER_SET[set_index]) & 0xFF
    return bytes([
        hres & 0xFF, (hres >> 8) & 0xFF,
        scale_r,
        base & 0xFF, base & 0xFF, base & 0xFF,
        0x01, 0x01, 0x01, 0x01,
    ])


def lut_set_header_for(set_index, base):
    """Public: return the 10-byte header for a given set at a given base."""
    return _build_set_header(set_index, base)


def raw_extended_for(target_iso):
    """Public: return the 161-byte raw_extended for a target ISO."""
    return _build_raw_extended(_base_for_iso(target_iso))

U16_MAX = 65535


def scale_display_for_iso(display, target_iso):
    """Return a new display-space array scaled by REF_ISO / target_iso.

    Per-stop scaling is exactly 2x in the CFR reference set
    (bw25 = 4x bw100, bw800 = 0.125x bw100, etc.), so this is the same
    math the original authoring tool uses."""
    factor = REF_ISO / float(target_iso)
    return tuple(int(round(v * factor)) for v in display)


def _base_for_iso(iso):
    """Look up the base scale for the given ISO.  Falls back to a
    headroom-aware formula for any ISO outside the wizard's table
    (currently 25..800)."""
    if iso in _BASE_BY_ISO:
        return _BASE_BY_ISO[iso]
    # Fallback: pick smallest base such that Master A stored peak is
    # <= ~26K (CFR's empirical ceiling, leaves headroom for Set 8).
    peak = max(scale_display_for_iso(MASTER_A_DISPLAY, iso))
    return max(1, math.ceil(peak / 26214))


def _encode(display, base):
    """Convert a display-space array to stored values at a given base scale,
    clamped to u16."""
    return tuple(min(U16_MAX, int(round(v / base))) for v in display)


def _r_scale_for_set(set_index, base):
    """R-channel scale stored for a given set.  Set 0 has its own
    constant; Sets 1-9 follow the documented `base + offset` pattern."""
    if set_index == 0:
        return _SET0_R_SCALE_CONSTANT
    return base + _R_SCALE_OFFSET_PER_SET[set_index]


def build_bw_35mm_lut_sets(target_iso):
    """Build all 10 LutSet objects for a new B&W 35mm FLM at the given ISO.

    Returns a tuple of 10 pp8k.LutSet ready to drop into a FilmTable.
    R, G, B are identical for B&W (single phosphor exposure).

    Layout follows the documented Polaroid format: stored values follow
    the 2-master invariant (Sets 0/2/4/6/7 = Master A, Sets 1/3/5 =
    ceil(MA/2), Set 8 = 2*Set9, Set 9 = Master B), and per-set scales
    follow the empirically-derived offset table.  See
    `pipalette_old/FILMTABLE_ANALYSIS.md` for the byte-by-byte spec.
    """
    display_a = scale_display_for_iso(MASTER_A_DISPLAY, target_iso)
    display_b = scale_display_for_iso(MASTER_B_DISPLAY, target_iso)

    base = _base_for_iso(target_iso)
    stored_a = _encode(display_a, base)
    stored_b = _encode(display_b, base)
    half_a = tuple((v + 1) >> 1 for v in stored_a)
    double_b = tuple(min(U16_MAX, v * 2) for v in stored_b)

    # Stored values per set, per the 2-master invariant.  Sets 0/2/4/6/7
    # carry Master A stored bytes; Sets 1/3/5 carry ceil(Master A / 2);
    # Set 8 carries 2*Master B; Set 9 is Master B.
    per_set_stored = (
        stored_a,  # Set 0
        half_a,    # Set 1
        stored_a,  # Set 2
        half_a,    # Set 3
        stored_a,  # Set 4
        half_a,    # Set 5
        stored_a,  # Set 6
        stored_a,  # Set 7 (Master A)
        double_b,  # Set 8
        stored_b,  # Set 9 (Master B)
    )

    sets = []
    for i in range(10):
        ch = pp8k.LutChannel(values=per_set_stored[i])
        scale = _r_scale_for_set(i, base)
        # G and B scales mirror R for our B&W output (R is the only
        # channel that uses the per-set offset; G/B always use base).
        # CFR writes scale_g = scale_b = base; we follow.
        sets.append(pp8k.LutSet(
            red=ch,
            green=ch,
            blue=ch,
            scale_r=scale,
            scale_g=base,
            scale_b=base,
            header=lut_set_header_for(i, base),
        ))
    return tuple(sets)


def preview_master_a_for_iso(target_iso):
    """Display-space Master A for the wizard's live curve preview.

    Returned tuple is the curve the firmware sees at HRES=4096 after the
    scale factor is folded back in -- exactly what the read-only viewer on
    the detail page would show after the file is saved."""
    return scale_display_for_iso(MASTER_A_DISPLAY, target_iso)


def preview_master_b_for_iso(target_iso):
    return scale_display_for_iso(MASTER_B_DISPLAY, target_iso)
