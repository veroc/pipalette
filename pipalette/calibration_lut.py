"""Per-ISO calibration LUT for speed-point calibration.

During speed-point calibration the roll is exposed through THIS LUT
instead of the user's target FLM, so the drive at each wedge patch is
deterministic and independent of whatever curve the wizard placed.

The LUT is constructed per-ISO at roll-creation time: pixel values
1..N_PATCHES are mapped directly to N_PATCHES log-spaced drives
spanning ±2 stops around the predicted speed-point drive for the
labeled ISO.  Pixel 0 stays at drive 0 (black background); pixels
N_PATCHES+1..255 hold the last drive (capped) so the file is a valid
monotone LUT.

Predicted speed-point drives (reference, ISO 100):
    Master A (Set 7, 4K):  drive 150
    Master B (Set 9, 8K):  drive  38

For other ISOs both predicted drives scale by 100/iso (slower films
need proportionally more drive to land the speed point).

This way every wedge patch's drive is known exactly at roll-creation
time and stored on the roll's snapshotted profile.flm.
"""

import math

import pp8k

from . import wizard_baselines


N_PATCHES = 24
HALF_RANGE_LOG = 2.0 * math.log(2.0)  # ±2 stops in natural log

# Reference predicted speed-point drives at ISO 100.  Both come from
# round-2 Foma calibration (Master A peak / 84 -> ~150;
# Master B peak / 94 -> ~38).
D_SP_4K_REF = 150.0
D_SP_8K_REF = 38.0
REF_ISO = 100.0

INTERNAL_NAME = "PIPALCAL"
DISPLAY_NAME_TEMPLATE = "piPalette cal {iso}"


def predicted_speed_point(iso, resolution):
    """Predicted speed-point drive for the labeled ISO at the requested
    resolution ('4k' or '8k').  Reference values scaled by 100/iso."""
    factor = REF_ISO / float(iso)
    if resolution == "4k":
        return D_SP_4K_REF * factor
    if resolution == "8k":
        return D_SP_8K_REF * factor
    raise ValueError(f"unknown resolution {resolution!r}")


def wedge_drives(D_center, n=N_PATCHES):
    """N log-spaced drives spanning ±2 stops around D_center."""
    if n < 2:
        raise ValueError("n must be >= 2")
    step = 2.0 * HALF_RANGE_LOG / (n - 1)
    return tuple(
        D_center * math.exp(-HALF_RANGE_LOG + i * step) for i in range(n)
    )


def wedge_pixel_values(n=N_PATCHES):
    """Pixel values used by the wedge -- one pixel per patch, starting
    at pixel 1 (pixel 0 stays black)."""
    return tuple(range(1, n + 1))


def _stored_for_drives(drives, n=N_PATCHES):
    """Build a 256-entry stored array: stored[0] = 0, stored[1..n] =
    round(drives), stored[n+1..255] = stored[n].  Clamped to u16."""
    a = [0] * 256
    for i in range(n):
        a[i + 1] = max(0, min(0xFFFF, round(drives[i])))
    for i in range(n + 1, 256):
        a[i] = a[n]
    return tuple(a)


def build_calibration_flm_bytes(template_table, iso, n=N_PATCHES):
    """Serialize a calibration FLM tuned for the given labeled ISO.

    Camera metadata mirrors the template (the user's target table).
    LUT shape is a step ramp where pixel i (i=1..n) carries the
    drive needed to land at a specific log-spaced position ±2 stops
    around the predicted speed-point drive for that ISO/resolution.
    """
    D_sp_4k = predicted_speed_point(iso, "4k")
    D_sp_8k = predicted_speed_point(iso, "8k")
    drives_4k = wedge_drives(D_sp_4k, n)
    drives_8k = wedge_drives(D_sp_8k, n)

    stored_a = _stored_for_drives(drives_4k, n)
    stored_b = _stored_for_drives(drives_8k, n)

    # 2-master invariant.
    half_a = tuple((v + 1) >> 1 for v in stored_a)
    double_b = tuple(min(0xFFFF, v * 2) for v in stored_b)
    per_set_stored = (
        stored_a, half_a, stored_a, half_a, stored_a, half_a,
        stored_a, stored_a, double_b, stored_b,
    )

    # scale = 1 for both masters: stored values already ARE the drives,
    # no further multiplication needed on the firmware side.
    base = 1
    SET0_R = wizard_baselines._SET0_R_SCALE_CONSTANT  # CFR convention

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
        if i == 0:
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
