"""Render docs/assets/chat-demo.gif: the first-reel chat (chat-first-reel.html) playing out message by message.

Run:  python demo_gif.py      Needs: pip install playwright pillow; uses your installed Chrome.
"""
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from render import CSS, FONTS, HERE, telegram_blocks

OUT = HERE.parent / "chat-demo.gif"
# How long each state stays up (ms): the user's messages briefly, the bot's cards long enough to read.
HOLD = {"out": 1100, "in": 2300, "last": 4000}


def main():
    body = telegram_blocks((HERE / "chat-first-reel.html").read_text(encoding="utf-8"))
    page_file = HERE / "_demo.html"
    page_file.write_text(f'<!DOCTYPE html><html><head><meta charset="utf-8">{FONTS}<style>{CSS}</style>'
                         f'</head><body>{body}</body></html>', encoding="utf-8")
    frames, durations = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(device_scale_factor=2, viewport={"width": 420, "height": 900})
        page.goto(page_file.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        kinds = page.evaluate("() => [...document.querySelectorAll('.chat .msg')].map(m => m.classList.contains('out') ? 'out' : 'in')")
        for shown in range(1, len(kinds) + 1):
            # later messages keep their space (the frame size never changes) but aren't drawn yet
            page.evaluate("n => document.querySelectorAll('.chat .msg').forEach((m, i) => m.style.visibility = i < n ? 'visible' : 'hidden')", shown)
            png = page.locator("#shot").screenshot()
            frames.append(Image.open(io.BytesIO(png)).convert("RGB"))
            durations.append(HOLD["last"] if shown == len(kinds) else HOLD[kinds[shown - 1]])
        browser.close()
    page_file.unlink()
    # one shared palette keeps colours steady between frames and the file small
    palette = frames[-1].convert("P", palette=Image.ADAPTIVE, colors=96)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(OUT, save_all=True, append_images=gif[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(f"wrote {OUT.relative_to(HERE.parents[2])} ({len(gif)} frames, {OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
