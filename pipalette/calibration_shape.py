"""Reference H&D shape for speed-point calibration.

When "Find Speed Point" calibration succeeds, it returns one fact: the
drive level that produces speed-point density (b+f + 0.10) on the user's
film+dev combo.  Building a full 256-LUT from that single measurement
requires an ASSUMED FILM RESPONSE -- a curve that describes how density
rises with log-drive away from the speed point.

This module defines that assumed response: a sigmoid in
density-vs-log-drive-relative-to-speed-point.  No film-specific data
involved -- the parameters were chosen to match a typical panchromatic
B&W negative (dmax ~ 2.0, slope ~ 0.6 density per stop in the straight
section).

Mathematically:

    D(L_rel) = DMAX / (1 + exp(-(L_rel - L_50) / K))

where L_rel = log_drive - log_D_sp (log-drive offset from speed point),
DMAX = 2.0, K = 0.25, and L_50 is fixed by the requirement
D(0) = SPEED_POINT_OFFSET = 0.10:

    L_50 = K * ln(DMAX / SPEED_POINT_OFFSET - 1) = 0.25 * ln(19) ~ 0.736

The shape produces (relative to b+f):
    L_rel = -1.0  ->  D ~ 0.004   (deep toe, paper-black)
    L_rel =  0.0  ->  D = 0.10    (speed point, anchored exactly)
    L_rel =  0.5  ->  D ~ 0.56    (Zone IV)
    L_rel =  1.0  ->  D ~ 1.43    (Zone VII)
    L_rel =  1.5  ->  D ~ 1.78    (Zone IX, into shoulder)

Refinement calibration (the 31-step wedge) measures the real film and
overrides this assumption with a fitted curve.  The shape here is only
the bootstrap: it places the LUT well enough that the first refinement
round has clean toe + working-range coverage.
"""

import math


DMAX = 2.0
K = 0.25
SPEED_POINT_OFFSET = 0.10
L_50 = K * math.log(DMAX / SPEED_POINT_OFFSET - 1.0)

# Pixel value where the speed point lands in the output LUT.  Mirrors
# calibration.ZONE_I_PIXEL -- duplicated here so this module has no
# dependency on the larger calibration module.
ZONE_I_PIXEL = 25


def reference_density(L_rel):
    """Density above b+f at log-drive offset L_rel from the speed point."""
    return DMAX / (1.0 + math.exp(-(L_rel - L_50) / K))


def reference_log_drive(target_density_above_bf):
    """Inverse of reference_density.

    Returns L_rel such that reference_density(L_rel) == target.  Defined
    on (0, DMAX); outside that range returns ±inf (caller clamps).
    """
    if target_density_above_bf <= 0.0:
        return float("-inf")
    if target_density_above_bf >= DMAX:
        return float("inf")
    # Sigmoid inverse: L_50 - K * ln(DMAX / target - 1)
    return L_50 - K * math.log(DMAX / target_density_above_bf - 1.0)


def place_shape(D_sp, target_range, zone_i_pixel=ZONE_I_PIXEL):
    """Build a 256-entry display-space LUT anchored at speed-point drive D_sp.

    Args:
        D_sp:           drive level that produces b+f + 0.10 on the film.
        target_range:   density range above speed point we want pixel 255
                        to reach (e.g. 1.05 for grade-2 paper).
        zone_i_pixel:   pixel value the speed point lands at (default 25).

    Returns: 256-tuple of int display drives.
    """
    if D_sp <= 0:
        raise ValueError("D_sp must be > 0")
    if target_range <= 0:
        raise ValueError("target_range must be > 0")

    log_D_sp = math.log(D_sp)
    lut = [0] * 256

    # Toe ramp: pixels 0..zone_i_pixel land in the paper-black zone and
    # don't carry print detail; render as a clean linear ramp 0 -> D_sp
    # so the LUT graph reads naturally and the firmware has well-defined
    # drives for sub-Zone-I pixel values.
    for x in range(zone_i_pixel + 1):
        lut[x] = round((x / zone_i_pixel) * D_sp)

    # Working range: pixels above zone_i_pixel target a linear-in-pixel
    # density progression above the speed point.  Invert the assumed
    # film response to find the drive needed for each target density.
    for x in range(zone_i_pixel + 1, 256):
        t = (x - zone_i_pixel) / (255 - zone_i_pixel)
        target_d_above_bf = SPEED_POINT_OFFSET + t * target_range
        L_rel = reference_log_drive(target_d_above_bf)
        drive = math.exp(log_D_sp + L_rel)
        # u32 is plenty of headroom for display drives, but clamp
        # against u16 overflow in encoding -- the wizard's base picker
        # handles down-scaling if the peak is too large.
        lut[x] = max(0, round(drive))

    # Enforce monotone non-decreasing (tiny rounding artifacts only).
    for i in range(1, 256):
        if lut[i] < lut[i - 1]:
            lut[i] = lut[i - 1]
    return tuple(lut)
