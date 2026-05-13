"""Capture screenshots of piPalette for the user manual.

Usage:
    # 1. Start piPalette: `.venv/bin/python app.py` (mock mode in data/config.json)
    # 2. Run: `.venv/bin/python docs/screenshots.py`
    # Output: docs/assets/screenshots/*.png

The shots use real data already in data/ so layouts look natural.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright


BASE = "http://localhost:5000"
OUT = Path(__file__).parent / "assets" / "screenshots"

# Viewport: tall enough to fit a typical page; deviceScaleFactor=2 keeps
# screenshots crisp in PDF.
VIEWPORT = {"width": 1280, "height": 860}
SCALE = 2


def shoot(page, name, *, full_page=True, clip=None):
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{name}.png"
    page.screenshot(path=str(target), full_page=full_page, clip=clip)
    print(f"wrote {target.relative_to(Path(__file__).parent)}")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=SCALE,
        )
        page = context.new_page()

        # --- Rolls list ---------------------------------------------------
        page.goto(f"{BASE}/rolls")
        page.wait_for_selector(".roll-list, .empty-state")
        shoot(page, "rolls-list")

        # --- New Roll dialog ---------------------------------------------
        page.click('[data-action="new-roll"]')
        page.wait_for_selector(".modal")
        # Give the form a moment to render the BW filter row if applicable.
        page.wait_for_timeout(200)
        shoot(page, "new-roll-dialog")
        # Dismiss the modal so subsequent shots are clean.
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal", state="detached")

        # --- Roll detail --------------------------------------------------
        first_card = page.locator(".roll-card").first
        first_card.click()
        page.wait_for_selector(".frame-grid")
        # Let thumbnails load.
        page.wait_for_load_state("networkidle")
        shoot(page, "roll-detail")

        # --- Film tables --------------------------------------------------
        page.goto(f"{BASE}/film-tables")
        page.wait_for_selector(".dropzone")
        shoot(page, "film-tables")

        # --- Device (mock mode) -------------------------------------------
        page.goto(f"{BASE}/device")
        page.wait_for_selector(".panel")
        shoot(page, "device-mock")

        # --- Topbar (cropped) ---------------------------------------------
        page.goto(f"{BASE}/rolls")
        page.wait_for_selector(".topbar")
        topbar = page.locator(".topbar")
        box = topbar.bounding_box()
        if box:
            shoot(
                page,
                "topbar",
                full_page=False,
                clip={
                    "x": 0,
                    "y": 0,
                    "width": VIEWPORT["width"],
                    "height": box["height"],
                },
            )

        browser.close()


if __name__ == "__main__":
    main()
