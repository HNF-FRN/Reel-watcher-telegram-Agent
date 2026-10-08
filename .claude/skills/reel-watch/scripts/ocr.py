"""ocr.py - read the text on video frames and screenshots, offline.

    REEL_OCR=auto (default) | rapidocr | tesseract | off
    REEL_OCR_LANG=eng       Tesseract languages, e.g. eng+fra

rapidocr   pip install rapidocr onnxruntime   Apache-2.0, ONNX models on the CPU, any OS, no system packages
           (the older rapidocr_onnxruntime package works too)
tesseract  the tesseract program on PATH       apt install tesseract-ocr · brew install tesseract ·
                                               winget install UB-Mannheim.TesseractOCR
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_engine = None


def log(msg):
    print(f"[ocr] {msg}", file=sys.stderr, flush=True)


def as_list(x):
    """RapidOCR returns tuples, lists or numpy arrays depending on the version; numpy arrays can't be tested
    with `or`, so normalise everything to plain lists first."""
    if x is None:
        return []
    return x.tolist() if hasattr(x, "tolist") else list(x)


def to_lines(items, min_score=0.5):
    """[(box, text, score)] -> text lines top to bottom, left to right. Boxes are four [x, y] corners; pieces
    whose vertical centre falls inside the current line's band join that line."""
    pieces = []
    for box, text, score in items:
        if not str(text).strip() or (score is not None and float(score) < min_score):
            continue
        ys, xs = [p[1] for p in box], [p[0] for p in box]
        pieces.append((min(ys), max(ys), min(xs), str(text).strip()))
    lines = []
    for top, bottom, left, text in sorted(pieces):
        centre = (top + bottom) / 2
        if lines and lines[-1]["top"] <= centre <= lines[-1]["bottom"]:
            lines[-1]["parts"].append((left, text))
        else:
            lines.append({"top": top, "bottom": bottom, "parts": [(left, text)]})
    return [" ".join(t for _, t in sorted(line["parts"])) for line in lines]


def clean(lines):
    """Drop OCR noise: lines that are mostly symbols ("| ~ —") or a single character."""
    out = []
    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        chars = line.replace(" ", "")
        if len(chars) >= 2 and sum(c.isalnum() for c in chars) >= 0.5 * len(chars):
            out.append(line)
    return out


def _rapidocr():
    try:
        from rapidocr import RapidOCR
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR  # the older package name
    engine = RapidOCR()

    def read(path):
        res = engine(str(path))
        if isinstance(res, tuple):  # rapidocr_onnxruntime: ([[box, text, score], ...] or None, timings)
            items = [(box, text, score) for box, text, score in (res[0] or [])]
        else:  # rapidocr 2+: an object with boxes, txts and scores
            items = list(zip(as_list(getattr(res, "boxes", None)), as_list(getattr(res, "txts", None)),
                             as_list(getattr(res, "scores", None))))
        return to_lines(items)
    return read


def _tesseract():
    exe = shutil.which("tesseract")
    if not exe and os.name == "nt":
        guess = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe"
        exe = str(guess) if guess.exists() else None
    if not exe:
        raise RuntimeError("tesseract is not on PATH")
    lang = os.environ.get("REEL_OCR_LANG", "eng")

    def read(path):
        r = subprocess.run([exe, str(path), "stdout", "-l", lang, "--psm", "3"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "tesseract failed").strip()[-200:])
        return r.stdout.splitlines()
    return read


BACKENDS = (("rapidocr", _rapidocr), ("tesseract", _tesseract))


def engine():
    """(name, read) for the first backend that works, or (None, None). Picked once per process."""
    global _engine
    if _engine is None:
        want = os.environ.get("REEL_OCR", "auto").strip().lower()
        _engine = (None, None)
        if want not in ("off", "none", "0", "false", "no"):
            for name, make in BACKENDS:
                if want not in ("auto", "", name):
                    continue
                try:
                    _engine = (name, make())
                    break
                except Exception as e:
                    if want == name:
                        log(f"{name} is not available: {e}")
    return _engine


def read_all(paths, read):
    """{path: [lines]} for every image; one unreadable image doesn't stop the rest."""
    out = {}
    for p in paths:
        try:
            out[str(p)] = clean(read(p))
        except Exception as e:
            log(f"couldn't read {Path(p).name}: {e}")
            out[str(p)] = []
    return out
