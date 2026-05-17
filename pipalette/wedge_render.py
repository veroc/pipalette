"""Speed-point wedge frame rendering.

Renders a calibration wedge frame on demand at roll-creation time.
Layout mirrors the static refinement wedge (two patches per frame with
a dashed B/W spacer in the middle and per-patch labels at the bottom),
but the patch pixel values and drive labels are computed per-ISO from
the calibration_lut module so each patch reports its actual drive.

Frame numbering follows the same convention as the static wedge:
higher-numbered patch on the left, so a right-to-left read of the
developed (rewound) film walks the wedge in ascending order.

Public API:

    render_frame(iso, resolution, left_patch_idx, right_patch_idx)
        -> PNG bytes.  Patch indices are 1-based step numbers; either
        side may be None for the END marker on the last frame.

    frame_count()
        -> number of frames in one resolution's wedge.  With 24 patches
        and 2 patches per frame, this is 12.
"""

import io

from PIL import Image, ImageDraw, ImageFont

from . import calibration_lut


# Frame dimensions and styling -- mirror generate_calibration_wedge.py
# (35mm 3:2 native).
DIMS_4K = (4096, 2731)
DIMS_8K = (8192, 5461)
SPACER_4K = 80
SPACER_8K = 160
LABEL_SIZE_4K = 110
LABEL_SIZE_8K = 220
LABEL_MARGIN_4K = 60
LABEL_MARGIN_8K = 120

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _params_for(resolution):
    if resolution == "4k":
        return DIMS_4K, SPACER_4K, LABEL_SIZE_4K, LABEL_MARGIN_4K
    if resolution == "8k":
        return DIMS_8K, SPACER_8K, LABEL_SIZE_8K, LABEL_MARGIN_8K
    raise ValueError(f"unknown resolution {resolution!r}")


def _load_font(size):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(
                path, size, layout_engine=ImageFont.Layout.BASIC,
            )
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_text_with_outline(draw, xy, text, font, fill, outline):
    x, y = xy
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, fill=outline, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def frame_count():
    """Number of frames in a single-resolution wedge (4K or 8K)."""
    return (calibration_lut.N_PATCHES + 1) // 2


def _patch_pixel(idx):
    """Pixel value used by the calibration LUT to address patch `idx`
    (1-based).  Mirrors calibration_lut.wedge_pixel_values."""
    if idx is None:
        return None
    return idx  # patches sit at pixel values 1..N_PATCHES


def _patch_drives(iso, resolution):
    """Drives produced by the calibration LUT at each patch, in order."""
    D_center = calibration_lut.predicted_speed_point(iso, resolution)
    return calibration_lut.wedge_drives(D_center)


def frame_pairs():
    """Yield (left_patch, right_patch) for each wedge frame.

    Same in-canister flip convention as the refinement wedge: higher-
    numbered patch on the left.  Last frame's left slot is None when
    the patch count is odd (END marker)."""
    n = calibration_lut.N_PATCHES
    for i in range(0, n, 2):
        lower = i + 1
        upper = i + 2 if i + 1 < n else None
        yield upper, lower


def render_frame(iso, resolution, left_patch, right_patch):
    """Return PNG bytes for one wedge frame at the given resolution.

    iso              labeled film ISO (drives are predicted from this)
    resolution       '4k' or '8k'
    left_patch       1-based patch index on the left, or None for END
    right_patch      1-based patch index on the right, or None for END
    """
    (width, height), spacer, label_size, label_margin = _params_for(resolution)
    font = _load_font(label_size)
    drives = _patch_drives(iso, resolution)
    res_tag = resolution.upper()

    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    half = (width - spacer) // 2
    right_x = half + spacer

    def _fill_patch(idx, x0, x1):
        if idx is None:
            return None, None
        px = _patch_pixel(idx)
        draw.rectangle((x0, 0, x1, height), fill=px)
        return px, drives[idx - 1]

    left_px, left_drive = _fill_patch(left_patch, 0, half)
    right_px, right_drive = _fill_patch(right_patch, right_x, width)

    # Dashed B/W spacer -- always visible regardless of adjacent patch tones.
    stripe_height = max(4, height // 60)
    y = 0
    stripe_idx = 0
    while y < height:
        fill = 0 if (stripe_idx % 2 == 0) else 255
        y2 = min(y + stripe_height, height)
        draw.rectangle((half, y, right_x, y2), fill=fill)
        y = y2
        stripe_idx += 1

    label_y = height - label_margin - font.size

    def _label(idx, px, drive, x_anchor):
        if idx is None:
            _draw_text_with_outline(
                draw, (x_anchor, label_y), f"{res_tag} END",
                font, 255, 0,
            )
            return
        # Patch text shows step number, resolution, pixel value and
        # the drive that produced it -- the densitometer reading goes
        # into the entry grid keyed by patch index.
        text = f"S{idx:02d} {res_tag} p{px:03d} D{int(round(drive)):>4}"
        # For our 24-patch wedge the patch pixel is always 1..24 -- very
        # dark.  Use light fill with dark outline so the label is legible
        # against any patch shade (low pixels are nearly black).
        fill = 255
        outline = 0
        _draw_text_with_outline(draw, (x_anchor, label_y), text, font,
                                fill, outline)

    _label(left_patch, left_px, left_drive, label_margin)
    _label(right_patch, right_px, right_drive, right_x + label_margin)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
