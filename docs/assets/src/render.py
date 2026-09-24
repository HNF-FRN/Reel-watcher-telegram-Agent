"""Render the README images in docs/assets/ from the HTML files next to this script, with Chrome.

Each page is wrapped with the setup guide's style sheet (../../setup/style.css) and fonts, so the images match the
PDF. The element with id="shot" is captured at 2x.
Run:  python render.py [name ...]      (no names = all: banner, chat-cloud-mode, cloud-failover)
Needs: pip install playwright; uses your installed Chrome.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent
CSS = (HERE.parents[1] / "setup" / "style.css").read_text(encoding="utf-8")
FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700'
         '&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">')
TRANSPARENT = {"banner", "cloud-failover"}  # rounded dark cards: keep the corners see-through


def main():
    names = sys.argv[1:] or sorted(p.stem for p in HERE.glob("*.html"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(device_scale_factor=2, viewport={"width": 1400, "height": 900})
        for name in names:
            body = (HERE / f"{name}.html").read_text(encoding="utf-8")
            tmp = HERE / f"_{name}.html"
            tmp.write_text(f'<!DOCTYPE html><html><head><meta charset="utf-8">{FONTS}<style>{CSS}</style>'
                           f'</head><body>{body}</body></html>', encoding="utf-8")
            page.goto(tmp.as_uri(), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            out = ASSETS / f"{name}.png"
            page.locator("#shot").screenshot(path=str(out), omit_background=name in TRANSPARENT)
            tmp.unlink()
            print("wrote", out.relative_to(ASSETS.parent.parent))
        browser.close()


if __name__ == "__main__":
    main()
