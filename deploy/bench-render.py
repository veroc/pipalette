#!/usr/bin/env python3
"""Compare the PIL vs libvips render paths on a real frame.

Run on the Pi (or any host with piPalette installed):

    sudo /opt/pipalette/.venv/bin/python3 /tmp/bench-render.py

Picks the first frame of the first roll with frames, renders it 3 times
through each path, and reports averages so you can see whether libvips
is actually winning on this hardware.
"""

import json
import sys
import time
from pathlib import Path

# When dropped at /tmp/bench-render.py, the piPalette install is
# typically at /opt/pipalette; fall back to a sibling for dev runs.
for candidate in ("/opt/pipalette", str(Path(__file__).resolve().parent.parent)):
    if (Path(candidate) / "pipalette" / "rolls.py").exists():
        sys.path.insert(0, candidate)
        DATA_DIR = Path(candidate) / "data"
        break
else:
    print("ERROR: couldn't find piPalette install")
    sys.exit(1)

from pipalette.rolls import RollStore, _HAS_VIPS  # noqa: E402
import pp8k  # noqa: E402


def pick_frame():
    with open(DATA_DIR / "rolls" / "index.json") as f:
        idx = json.load(f)
    for r in idx["rolls"]:
        if r["frames"]:
            return r, r["frames"][0]
    return None, None


def time_calls(name, fn, n):
    times = []
    for i in range(n):
        t0 = time.monotonic()
        fn()
        dt = time.monotonic() - t0
        times.append(dt)
        print(f"  {name} iter {i+1}: {dt*1000:7.0f} ms")
    return times


def main():
    print(f"_HAS_VIPS = {_HAS_VIPS}")
    print()

    roll, frame = pick_frame()
    if frame is None:
        print("No rolls with frames found in", DATA_DIR / "rolls")
        sys.exit(1)
    print(f"Roll:  {roll['name']}  ({roll['id']})")
    print(f"Frame: {frame['original_name']} "
          f"({frame['src_width']}×{frame['src_height']})")
    width, height = pp8k.get_frame_dimensions(
        roll["aspect_w"], roll["aspect_h"], frame["resolution"]
    )
    print(f"Output canvas: {width}×{height}  "
          f"(resolution={frame['resolution']}, "
          f"transform={frame['transform']}, "
          f"rotation={frame['rotation']})")
    print()

    store = RollStore(DATA_DIR / "rolls")
    src = store.roll_dir(roll["id"]) / "images" / frame["image_filename"]
    out_path = Path("/tmp/bench-output.png")
    thumb_path = Path("/tmp/bench-thumb.jpg")

    N = 3
    print(f"Running {N} iterations of each path...")
    print()

    pil_times = time_calls(
        "PIL ",
        lambda: store._render_pil(src, out_path, thumb_path,
                                  width, height, dict(frame)),
        N,
    )

    if _HAS_VIPS:
        vips_times = time_calls(
            "vips",
            lambda: store._render_vips(src, out_path, thumb_path,
                                       width, height, dict(frame)),
            N,
        )
    else:
        vips_times = None

    print()
    pil_best = min(pil_times)
    pil_avg = sum(pil_times) / len(pil_times)
    print(f"PIL  best {pil_best*1000:7.0f} ms   avg {pil_avg*1000:7.0f} ms")
    if vips_times:
        vips_best = min(vips_times)
        vips_avg = sum(vips_times) / len(vips_times)
        print(f"vips best {vips_best*1000:7.0f} ms   avg {vips_avg*1000:7.0f} ms")
        print()
        print(f"vips is {pil_avg/vips_avg:5.2f}× faster on average")
        print(f"vips is {pil_best/vips_best:5.2f}× faster on best run")
    else:
        print("vips not available — only the PIL path was measured")


if __name__ == "__main__":
    main()
