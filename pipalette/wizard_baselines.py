"""Bundled baseline curves for the FLM creation wizard.

The two arrays below are the Master A (4K, Set 7) and Master B (8K, Set 9)
curves from `pp8k-research/bw100.flm`, expressed as 256 display-space u32
values (stored * scale).  bw100.flm was authored in the original Polaroid
CFR tool at ISO 100 with the Blue filter.

The wizard builds new B&W 35mm FLMs by scaling these display arrays by an
ISO factor (100 / target_iso), then encoding back to stored + scale per the
factory layout convention (see `build_bw_35mm_lut_sets` below).

Verified across all six CFR ISO references (bw25, bw50, ..., bw800) that
the curve shape is identical and differences are a uniform per-stop factor
of 2.  Verified across 60/62 Polaroid factory FLMs that the per-set scale
offsets are (0, +48, +32, +32, +16, +16, 0, 0, 0, 0) relative to Master A.
"""

import math

import pp8k


# Master A: 4K-resolution curve, loaded by firmware at HRES=4096.
MASTER_A_DISPLAY = (
        0,     2,     4,     6,    10,    16,    24,    36,
       48,    64,    82,   104,   128,   156,   184,   214,
      244,   276,   308,   340,   372,   402,   432,   460,
      488,   514,   538,   562,   584,   604,   624,   644,
      662,   680,   696,   712,   728,   744,   760,   774,
      790,   804,   820,   836,   850,   866,   880,   896,
      912,   928,   944,   962,   978,   996,  1012,  1030,
     1048,  1066,  1086,  1104,  1124,  1142,  1162,  1184,
     1204,  1224,  1246,  1268,  1290,  1312,  1336,  1358,
     1382,  1406,  1432,  1456,  1482,  1508,  1534,  1560,
     1588,  1616,  1644,  1672,  1702,  1732,  1762,  1792,
     1824,  1856,  1888,  1920,  1954,  1988,  2022,  2058,
     2094,  2130,  2168,  2206,  2244,  2284,  2324,  2364,
     2404,  2446,  2490,  2534,  2578,  2622,  2668,  2714,
     2762,  2810,  2860,  2910,  2960,  3012,  3064,  3118,
     3172,  3228,  3284,  3340,  3400,  3458,  3518,  3580,
     3642,  3706,  3770,  3836,  3904,  3972,  4042,  4112,
     4184,  4256,  4330,  4406,  4482,  4562,  4640,  4722,
     4804,  4888,  4974,  5060,  5148,  5238,  5330,  5422,
     5518,  5614,  5712,  5810,  5912,  6016,  6120,  6228,
     6336,  6446,  6558,  6674,  6790,  6908,  7028,  7152,
     7276,  7404,  7532,  7664,  7798,  7934,  8072,  8212,
     8356,  8502,  8650,  8802,  8954,  9110,  9270,  9432,
     9596,  9764,  9934, 10108, 10284, 10462, 10646, 10832,
    11020, 11212, 11408, 11608, 11810, 12016, 12226, 12438,
    12656, 12876, 13102, 13330, 13562, 13798, 14040, 14284,
    14534, 14788, 15046, 15308, 15574, 15846, 16124, 16404,
    16690, 16982, 17278, 17580, 17886, 18198, 18516, 18838,
    19168, 19502, 19842, 20188, 20540, 20900, 21264, 21634,
    22012, 22396, 22786, 23184, 23588, 24000, 24420, 24846,
    25278, 25720, 26168, 26626, 27090, 27562, 28044, 28532,
    29030, 29536, 30052, 30576, 31110, 31652, 32204, 32766,
)

# Master B: 8K-resolution curve, loaded by firmware at HRES=8192.
MASTER_B_DISPLAY = (
        0,     0,     2,     4,     6,     8,    12,    18,
       24,    32,    42,    52,    64,    78,    92,   106,
      122,   138,   154,   170,   186,   200,   216,   230,
      244,   256,   268,   280,   292,   302,   312,   322,
      330,   340,   348,   356,   364,   372,   380,   388,
      394,   402,   410,   418,   424,   432,   440,   448,
      456,   464,   472,   480,   488,   498,   506,   514,
      524,   532,   542,   552,   562,   572,   582,   592,
      602,   612,   622,   634,   644,   656,   668,   680,
      692,   704,   716,   728,   740,   754,   766,   780,
      794,   808,   822,   836,   850,   866,   880,   896,
      912,   928,   944,   960,   976,   994,  1012,  1028,
     1046,  1066,  1084,  1102,  1122,  1142,  1162,  1182,
     1202,  1224,  1244,  1266,  1288,  1310,  1334,  1358,
     1380,  1404,  1430,  1454,  1480,  1506,  1532,  1558,
     1586,  1614,  1642,  1670,  1700,  1728,  1760,  1790,
     1822,  1852,  1886,  1918,  1952,  1986,  2020,  2056,
     2092,  2128,  2166,  2202,  2242,  2280,  2320,  2360,
     2402,  2444,  2486,  2530,  2574,  2618,  2664,  2710,
     2758,  2806,  2856,  2906,  2956,  3008,  3060,  3114,
     3168,  3222,  3280,  3336,  3394,  3454,  3514,  3576,
     3638,  3702,  3766,  3832,  3898,  3966,  4036,  4106,
     4178,  4250,  4324,  4400,  4478,  4556,  4634,  4716,
     4798,  4882,  4966,  5054,  5142,  5232,  5322,  5416,
     5510,  5606,  5704,  5804,  5904,  6008,  6112,  6220,
     6328,  6438,  6550,  6664,  6780,  6900,  7020,  7142,
     7266,  7394,  7522,  7654,  7788,  7924,  8062,  8202,
     8346,  8490,  8638,  8790,  8942,  9098,  9258,  9420,
     9584,  9750,  9920, 10094, 10270, 10450, 10632, 10818,
    11006, 11198, 11394, 11592, 11794, 12000, 12210, 12422,
    12640, 12860, 13084, 13312, 13544, 13780, 14022, 14266,
    14514, 14768, 15026, 15288, 15554, 15826, 16102, 16384,
)

# bw100.flm was authored at ISO 100.  Scaling target is REF_ISO / target_iso.
REF_ISO = 100

# Base scale per ISO -- table copied from CFR's six reference files.  Picks
# the scale factor for Master A (Set 7).  Lower-ISO films need larger
# display values, which means larger base to keep stored u16; CFR also
# leaves headroom so Set 8 (= 2 * Master B stored) can't overflow.
_BASE_BY_ISO = {25: 5, 50: 3, 100: 2, 200: 1, 400: 1, 800: 1}

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
