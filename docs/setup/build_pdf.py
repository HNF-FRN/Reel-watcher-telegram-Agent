"""Build "Reel Agent Setup Guide.pdf" from body.html + the shared style sheet (style.css), with Chrome.

Two passes: the first finds which page each chapter starts on, the second fills in the contents page.
Run:  python build_pdf.py [--png]    (--png also writes page previews to ./preview/)
Needs: pip install playwright pypdf (and pypdfium2 for --png); uses your installed Chrome.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "Reel Agent Setup Guide.pdf"

MARK_JS = """() => document.querySelectorAll('[data-toc]').forEach(p => {
  const id = p.dataset.toc, el = document.getElementById(id);
  const m = document.createElement('span');
  m.className = 'toc-mark'; m.textContent = 'QQ' + id + 'QQ';
  m.style.cssText = 'font-size:1pt;color:transparent;position:absolute;';
  el.prepend(m);
})"""
FILL_JS = """pages => {
  document.querySelectorAll('.toc-mark').forEach(m => m.remove());
  for (const [id, n] of Object.entries(pages))
    document.querySelector(`[data-toc="${id}"]`).textContent = String(n).padStart(2, '0');
}"""


def assemble():
    css = (HERE / "style.css").read_text(encoding="utf-8")
    body = (HERE / "body.html").read_text(encoding="utf-8")
    page = HERE / "_assembled.html"
    page.write_text(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Reel Agent — Setup Guide</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{css}</style></head><body>{body}</body></html>""", encoding="utf-8")
    return page


def render(page, path):
    page.pdf(path=str(path), prefer_css_page_size=True, print_background=True, tagged=True, outline=True)


def main():
    global OUT
    src = assemble()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.goto(src.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="print")
        ids = page.evaluate("() => [...document.querySelectorAll('[data-toc]')].map(e => e.dataset.toc)")
        page.evaluate(MARK_JS)
        tmp = HERE / "_pass1.pdf"
        render(page, tmp)
        found = {}
        for i, pg in enumerate(PdfReader(str(tmp)).pages, start=1):
            for m in re.findall(r"QQ(\w+)QQ", (pg.extract_text() or "").replace(" ", "")):
                found.setdefault(m, i)
        tmp.unlink()
        missing = [i for i in ids if i not in found]
        if missing:
            sys.exit(f"could not locate chapters: {missing}")
        page.evaluate(FILL_JS, found)
        fresh = HERE / "_final.pdf"
        render(page, fresh)
        browser.close()
    src.unlink()
    try:
        fresh.replace(OUT)
    except PermissionError:
        OUT = OUT.with_name(OUT.stem + " (new).pdf")
        fresh.replace(OUT)
        print("NOTE: the guide is open in a viewer, so the new version was saved as", OUT.name)
    print(f"wrote {OUT} ({len(PdfReader(str(OUT)).pages)} pages, {OUT.stat().st_size / 1024:.0f} KB)")
    print("chapters:", found)

    if "--png" in sys.argv:
        import pypdfium2 as pdfium
        prev = HERE / "preview"
        prev.mkdir(exist_ok=True)
        for f in prev.glob("*.png"):
            f.unlink()
        doc = pdfium.PdfDocument(str(OUT))
        for i in range(len(doc)):
            doc[i].render(scale=1.4).to_pil().save(prev / f"p{i + 1:02d}.png")
        print(f"previews in {prev}")


if __name__ == "__main__":
    main()
