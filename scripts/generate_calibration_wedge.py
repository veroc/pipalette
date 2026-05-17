"""Generate the static calibration wedge PNGs.

Each wedge has 31 patches at linear pixel spacing (0..255).  Two patches
per physical frame, with a black spacer in between so a densitometer
probe can read each side without picking up the neighbour.  Each patch
carries a baked-in label ('S## · 4K · p###') so the user can match
densitometer readings to their step number on the developed film.

Frame layout per resolution:
    Frames 01..15 : pairs (step 2N-1, step 2N) at the resolution
    Frame  16     : step 31 on the left, "END" marker on the right

Reference wedges (one frame each, 4K only):
    Frame  LINEAR : 11 patches at equal linear-luminance steps,
                    sRGB-encoded (0, 89, 124, ..., 255).  Screen shows
                    equal physical-light increments -- a print that
                    reproduces this evenly verifies linear-light fidelity.
    Frame  sRGB   : 11 patches at equal pixel-value steps
                    (0, 26, 51, ..., 255).  Screen shows
                    perceptually-uniform increments -- a print that
                    reproduces this evenly verifies sRGB fidelity.

Output:
    static/calibration/wedge/35mm-3x2-4k/frame_NN.png        (16 files)
    static/calibration/wedge/35mm-3x2-8k/frame_NN.png        (16 files)
    static/calibration/reference/linear_35mm-3x2-4k.png      (1 file)
    static/calibration/reference/srgb_35mm-3x2-4k.png        (1 file)
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# 31 step pixel values, linear span 0..255.
STEP_COUNT = 31
PIXEL_VALUES = [round(i * 255 / (STEP_COUNT - 1)) for i in range(STEP_COUNT)]


def _srgb_oetf(linear):
    """Linear luminance [0,1] -> sRGB-encoded pixel value [0,255]."""
    linear = max(0.0, min(1.0, linear))
    if linear <= 0.0031308:
        encoded = linear * 12.92
    else:
        encoded = 1.055 * linear ** (1 / 2.4) - 0.055
    return round(encoded * 255)


# Reference wedges: 11 patches each, full pixel range.
REFERENCE_STEP_COUNT = 11
# Equal linear-light increments, sRGB-encoded for transmission via the
# 8-bit driver.  An sRGB display ramps this in 10%-of-linear-light steps.
LINEAR_WEDGE_PIXELS = tuple(
    _srgb_oetf(i / (REFERENCE_STEP_COUNT - 1))
    for i in range(REFERENCE_STEP_COUNT)
)
# Equal pixel-value increments.  An sRGB display ramps this in
# perceptually-uniform brightness steps.
SRGB_WEDGE_PIXELS = tuple(
    round(i * 255 / (REFERENCE_STEP_COUNT - 1))
    for i in range(REFERENCE_STEP_COUNT)
)

# Frame dimensions for 35mm 3:2.
DIMS_4K = (4096, 2731)
DIMS_8K = (8192, 5461)

# Black spacer between the two patches (in pixels).
SPACER_4K = 80
SPACER_8K = 160

# Label font size (pixels tall).
LABEL_SIZE_4K = 110
LABEL_SIZE_8K = 220

# Margin from patch edge for the label.
LABEL_MARGIN_4K = 60
LABEL_MARGIN_8K = 120

# Look for a TrueType font in standard Linux paths; fall back to the
# default bitmap font (which is tiny but readable enough).
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _load_font(size):
    # BASIC layout engine: avoids HarfBuzz (RAQM) which shares FreeType
    # state with pyvips in pipalette's Flask process and produces giant
    # text masks.  See pipalette/calibration.py for context.
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(
                    path, size, layout_engine=ImageFont.Layout.BASIC,
                )
            except Exception:
                pass
    return ImageFont.load_default()


def _frame_pairings():
    """Yield (left_step, right_step) for each frame, 1-indexed step
    numbers.  Higher-numbered step on the left, lower on the right --
    the recorder pulls the film fully into the canister at load and
    exposes "backwards", so frames sit in reverse order on the developed
    strip.  Putting the higher step on the left of each frame means a
    right-to-left read of the developed strip walks the wedge in clean
    ascending order S01, S02, ..., S31, END.

    Last frame's left slot is None (the END marker)."""
    for i in range(0, STEP_COUNT, 2):
        lower = i + 1
        upper = i + 2 if i + 1 < STEP_COUNT else None
        yield upper, lower


def _draw_text_with_outline(draw, xy, text, font, fill, outline):
    """Draw `text` with a 2-pixel outline so it reads on any background."""
    x, y = xy
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, fill=outline, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def _render_frame(width, height, spacer, font, label_margin,
                  resolution_tag, left_step, right_step):
    """Render a single calibration frame with two labeled patches.

    Either side may be `None`, which renders the END marker (black
    background, "END" label) on that side."""
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    half = (width - spacer) // 2
    right_x = half + spacer

    def _fill_patch(step, x0, x1):
        if step is None:
            return None
        px = PIXEL_VALUES[step - 1]
        draw.rectangle((x0, 0, x1, height), fill=px)
        return px

    left_px = _fill_patch(left_step, 0, half)
    right_px = _fill_patch(right_step, right_x, width)

    # Spacer: alternating black/white horizontal stripes so the
    # divider stays visible regardless of the adjacent patch tones
    # (a plain black spacer disappears on frame_01 where both halves
    # are near-black).
    stripe_height = height // 60  # ~46px on 4K, ~91px on 8K
    if stripe_height < 4:
        stripe_height = 4
    y = 0
    stripe_idx = 0
    while y < height:
        fill = 0 if (stripe_idx % 2 == 0) else 255
        y2 = min(y + stripe_height, height)
        draw.rectangle((half, y, right_x, y2), fill=fill)
        y = y2
        stripe_idx += 1

    # Labels: bottom-left of each half.
    label_y = height - label_margin - font.size

    def _label_patch(step, px, x_anchor):
        if step is None:
            _draw_text_with_outline(
                draw, (x_anchor, label_y), f"{resolution_tag} END",
                font, 255, 0,
            )
            return
        text = f"S{step:02d} · {resolution_tag} · p{px:03d}"
        fill = 255 if px < 128 else 0
        outline = 0 if px < 128 else 255
        _draw_text_with_outline(draw, (x_anchor, label_y), text, font,
                                fill, outline)

    _label_patch(left_step, left_px, label_margin)
    _label_patch(right_step, right_px, right_x + label_margin)

    return img


def render_set(resolution_tag, dims, spacer, label_size, label_margin, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _load_font(label_size)
    frames = list(_frame_pairings())
    for i, (left_step, right_step) in enumerate(frames, start=1):
        img = _render_frame(
            *dims, spacer, font, label_margin,
            resolution_tag, left_step, right_step,
        )
        path = out_dir / f"frame_{i:02d}.png"
        img.save(path, "PNG", optimize=True)
        print(f"  wrote {path.relative_to(out_dir.parent.parent.parent)}  ({path.stat().st_size} bytes)")


def _render_reference_wedge(width, height, title_font, label_font,
                            title, pixel_values):
    """Render an 11-step horizontal gray ramp on a 35mm 3:2 frame.

    Top strip carries the wedge title (LINEAR / sRGB).  Middle strip is
    the gray patches edge-to-edge.  Bottom strip carries the pixel-value
    labels centered under each patch.  Both strips are black so the
    white labels read against any patch tone."""
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)

    header_h = title_font.size + 80
    footer_h = label_font.size + 80
    patch_h = height - header_h - footer_h

    n = len(pixel_values)

    # Patches: float-stride so total width is hit exactly at the right edge.
    for i, px in enumerate(pixel_values):
        x0 = round(i * width / n)
        x1 = round((i + 1) * width / n)
        draw.rectangle((x0, header_h, x1, header_h + patch_h), fill=px)

    # Title at top, centered.
    tw_bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = tw_bbox[2] - tw_bbox[0]
    th = tw_bbox[3] - tw_bbox[1]
    draw.text(((width - tw) // 2, (header_h - th) // 2), title,
              fill=255, font=title_font)

    # Labels under each patch.
    label_y_top = header_h + patch_h + (footer_h - label_font.size) // 2
    for i, px in enumerate(pixel_values):
        text = f"p{px:03d}"
        bbox = draw.textbbox((0, 0), text, font=label_font)
        lw = bbox[2] - bbox[0]
        cx = (round(i * width / n) + round((i + 1) * width / n)) // 2
        draw.text((cx - lw // 2, label_y_top), text, fill=255, font=label_font)

    return img


def render_references(resolution_tag, dims, label_size, title_size, out_dir):
    """Render the LINEAR and sRGB reference wedges for one resolution."""
    out_dir.mkdir(parents=True, exist_ok=True)
    title_font = _load_font(title_size)
    label_font = _load_font(label_size)

    for slug, title, pixels in (
        ("linear", f"LINEAR · {resolution_tag} · equal linear-light steps",
         LINEAR_WEDGE_PIXELS),
        ("srgb", f"sRGB · {resolution_tag} · equal pixel-value steps",
         SRGB_WEDGE_PIXELS),
    ):
        img = _render_reference_wedge(*dims, title_font, label_font,
                                      title, pixels)
        path = out_dir / f"{slug}_35mm-3x2-{resolution_tag.lower()}.png"
        img.save(path, "PNG", optimize=True)
        print(f"  wrote {path.relative_to(out_dir.parent.parent.parent)}"
              f"  ({path.stat().st_size} bytes)")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    base = repo_root / "static" / "calibration" / "wedge"

    print("Rendering 4K wedge (35mm 3:2)...")
    render_set("4K", DIMS_4K, SPACER_4K, LABEL_SIZE_4K, LABEL_MARGIN_4K,
               base / "35mm-3x2-4k")

    print("\nRendering 8K wedge (35mm 3:2)...")
    render_set("8K", DIMS_8K, SPACER_8K, LABEL_SIZE_8K, LABEL_MARGIN_8K,
               base / "35mm-3x2-8k")

    print("\nRendering 4K reference wedges (LINEAR + sRGB)...")
    render_references("4K", DIMS_4K, LABEL_SIZE_4K, LABEL_SIZE_4K + 40,
                      repo_root / "static" / "calibration" / "reference")

    print(f"\nDone.  Wedge step pixel values: {PIXEL_VALUES}")
    print(f"LINEAR reference pixels: {LINEAR_WEDGE_PIXELS}")
    print(f"sRGB reference pixels:   {SRGB_WEDGE_PIXELS}")


if __name__ == "__main__":
    main()
