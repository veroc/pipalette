"""Generate the static calibration wedge PNGs.

Each wedge has 31 patches at linear pixel spacing (0..255).  Two patches
per physical frame, with a black spacer in between so a densitometer
probe can read each side without picking up the neighbour.  Each patch
carries a baked-in label ('S## · 4K · p###') so the user can match
densitometer readings to their step number on the developed film.

Frame layout per resolution:
    Frames 01..15 : pairs (step 2N-1, step 2N) at the resolution
    Frame  16     : step 31 on the left, "END" marker on the right

Output:
    static/calibration/wedge/35mm-3x2-4k/frame_NN.png  (16 files)
    static/calibration/wedge/35mm-3x2-8k/frame_NN.png  (16 files)
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# 31 step pixel values, linear span 0..255.
STEP_COUNT = 31
PIXEL_VALUES = [round(i * 255 / (STEP_COUNT - 1)) for i in range(STEP_COUNT)]

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
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _frame_pairings():
    """Yield (left_step, right_step) for each frame, 1-indexed step
    numbers.  Last frame's right slot is None (the END marker)."""
    for i in range(0, STEP_COUNT, 2):
        left = i + 1
        right = i + 2 if i + 1 < STEP_COUNT else None
        yield left, right


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
    """Render a single calibration frame with two labeled patches."""
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    half = (width - spacer) // 2

    left_px = PIXEL_VALUES[left_step - 1]
    draw.rectangle((0, 0, half, height), fill=left_px)

    right_x = half + spacer
    if right_step is not None:
        right_px = PIXEL_VALUES[right_step - 1]
        draw.rectangle((right_x, 0, width, height), fill=right_px)
    else:
        # END marker: the right half is plain gray (mid-tone) with a
        # large label so it's unambiguous as "end of wedge".
        right_px = None

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

    left_text = f"S{left_step:02d} · {resolution_tag} · p{left_px:03d}"
    left_fill = 255 if left_px < 128 else 0
    left_outline = 0 if left_px < 128 else 255
    _draw_text_with_outline(
        draw, (label_margin, label_y), left_text, font, left_fill, left_outline,
    )

    if right_step is not None:
        right_text = f"S{right_step:02d} · {resolution_tag} · p{right_px:03d}"
        right_fill = 255 if right_px < 128 else 0
        right_outline = 0 if right_px < 128 else 255
        _draw_text_with_outline(
            draw, (right_x + label_margin, label_y), right_text, font,
            right_fill, right_outline,
        )
    else:
        _draw_text_with_outline(
            draw, (right_x + label_margin, label_y),
            f"{resolution_tag} END",
            font, 255, 0,
        )

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


def main():
    repo_root = Path(__file__).resolve().parent.parent
    base = repo_root / "static" / "calibration" / "wedge"

    print("Rendering 4K wedge (35mm 3:2)...")
    render_set("4K", DIMS_4K, SPACER_4K, LABEL_SIZE_4K, LABEL_MARGIN_4K,
               base / "35mm-3x2-4k")

    print("\nRendering 8K wedge (35mm 3:2)...")
    render_set("8K", DIMS_8K, SPACER_8K, LABEL_SIZE_8K, LABEL_MARGIN_8K,
               base / "35mm-3x2-8k")

    print(f"\nDone.  Step pixel values: {PIXEL_VALUES}")


if __name__ == "__main__":
    main()
