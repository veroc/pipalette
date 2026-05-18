"""Per-ISO calibration LUT for speed-point calibration.

The calibration LUT is a simple linear ramp:

    stored[i] = i   (i = 0..255)
    scale_r   = K_iso       =>  drive(pixel) = K_iso * pixel

The wedge samples 24 evenly-spaced pixel values from 0 to 255, giving
24 evenly-spaced DRIVES from 0 to K_iso*255.  No predicted-speed-point
centring -- the wedge always covers the full plausible drive range and
finds the speed-point density crossing wherever it lands.

Why linear (not log-spaced like an earlier draft):  Mode A only needs
to land the LUT within ~50% of the true D_sp.  Mode B refines from
there.  Linear-ramp precision near the speed point is ~15-25% which is
ample, and the design carries no prediction-dependency: the wedge
brackets D_sp for any film whose actual speed is within the range
0..K_iso*255.

K_iso table -- picked so max drive at pixel 255 stays well under the
~3000 halation threshold we've observed on real hardware at ISO 100,
while still reaching ~10x the predicted speed-point drive (giving
generous coverage for films that are 2 stops slower than expected).
For other ISOs, K scales by 100/iso (slower films need higher drives).
"""

import pp8k

from . import wizard_baselines


N_PATCHES = 24
INTERNAL_NAME = "PIPALCAL"
DISPLAY_NAME_TEMPLATE = "piPalette cal {iso}"

# K factor at 4K per labeled ISO.  Max drive at pixel 255 = K * 255.
K_BY_ISO_4K = {
    25:  32,   # max drive 8160
    50:  16,   # max drive 4080
    100:  8,   # max drive 2040
    200:  4,   # max drive 1020
    400:  2,   # max drive  510
    800:  1,   # max drive  255
}

# K factor at 8K -- typically 1/4 of 4K (firmware draws 4x more
# scanlines at 8K so each line needs 1/4 the drive).  Clamped to a
# minimum of 1 for fast ISOs where 4K/4 isn't an integer.
K_BY_ISO_8K = {
    25:  8,
    50:  4,
    100: 2,
    200: 1,
    400: 1,
    800: 1,
}


def scale_for(iso, resolution):
    """Return K (the LUT's scale_r) for the given ISO + resolution."""
    if resolution == "4k":
        return K_BY_ISO_4K[iso]
    if resolution == "8k":
        return K_BY_ISO_8K[iso]
    raise ValueError(f"unknown resolution {resolution!r}")


def wedge_pixel_values(n=N_PATCHES):
    """N evenly-spaced pixel values from 0 to 255 inclusive."""
    if n < 2:
        raise ValueError("n must be >= 2")
    return tuple(round(i * 255 / (n - 1)) for i in range(n))


def wedge_drives(iso, resolution, n=N_PATCHES):
    """Drives produced by the calibration LUT at each wedge patch."""
    K = scale_for(iso, resolution)
    return tuple(K * p for p in wedge_pixel_values(n))


def build_calibration_flm_bytes(template_table, iso):
    """Serialize a per-ISO calibration FLM (linear ramp, K-scaled).

    Camera metadata mirrors the template (the user's target table).
    LUT shape is a linear ramp 0..255 stored, with scale_r per
    resolution: K_4K on Set 7 (Master A), K_8K on Set 9 (Master B).
    """
    linear_stored = tuple(range(256))
    half = tuple((v + 1) >> 1 for v in linear_stored)
    double = tuple(min(0xFFFF, v * 2) for v in linear_stored)

    # 2-master invariant.
    per_set_stored = (
        linear_stored, half, linear_stored, half, linear_stored, half,
        linear_stored, linear_stored, double, linear_stored,
    )

    K_4k = K_BY_ISO_4K[iso]
    K_8k = K_BY_ISO_8K[iso]
    # base scale for the FLM header.  Set 7 (Master A) overrides this
    # to K_4k via its header byte; Set 9 (Master B) overrides to K_8k.
    # Other resolutions inherit base + the wizard's per-set offsets.
    base = K_4k
    SET0_R = wizard_baselines._SET0_R_SCALE_CONSTANT

    def header(set_index, scale_r):
        if set_index == 0:
            return None
        hres = wizard_baselines._HRES_PER_SET[set_index]
        return bytes([
            hres & 0xFF, (hres >> 8) & 0xFF,
            scale_r & 0xFF,
            base & 0xFF, base & 0xFF, base & 0xFF,
            0x01, 0x01, 0x01, 0x01,
        ])

    sets = []
    for i in range(10):
        if i == 9:
            scale = K_8k
        elif i == 7:
            scale = K_4k
        elif i == 0:
            scale = SET0_R
        else:
            scale = base + wizard_baselines._R_SCALE_OFFSET_PER_SET[i]
        ch = pp8k.LutChannel(values=per_set_stored[i])
        sets.append(pp8k.LutSet(
            red=ch, green=ch, blue=ch,
            scale_r=scale, scale_g=base, scale_b=base,
            header=header(i, scale),
        ))

    raw_ext = wizard_baselines._build_raw_extended(base)
    name = DISPLAY_NAME_TEMPLATE.format(iso=iso)[:24]
    new_table = pp8k.FilmTable(
        name=name,
        internal_name=INTERNAL_NAME,
        camera_type=template_table.camera_type,
        camera_type_name=template_table.camera_type_name,
        is_bw=template_table.is_bw,
        bw_filter=template_table.bw_filter,
        bw_filter_name=template_table.bw_filter_name,
        aspect_w=template_table.aspect_w,
        aspect_h=template_table.aspect_h,
        lut_sets=tuple(sets),
        encrypted_data=b"",
        flags=template_table.flags,
        raw_extended=raw_ext,
    )
    return pp8k.serialize_flm(new_table)
