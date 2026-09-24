"""tgfmt.py - turn the lightly marked-up text the bot writes into tidy Telegram messages.

Telegram shows plain text as-is, so **bold** keeps its stars, and in "/plan 4" only "/plan" is tappable (it sends
the command without its number). This converts light Markdown to Telegram's HTML and makes commands one tap:

    **bold** · *italic* · `code` · ```code block``` · # Heading · > quote · [text](https://…) · "- item" -> "• item"
    /plan 4 -> /plan_4 · /snooze R3 1h -> /snooze_R3_1h   (the bot reads "_" as a space)

The same rules are mirrored in cloud/worker.mjs (toHtml, tapify) so both sides look alike.
"""
import html
import re

# Commands whose arguments are a job/reminder number plus at most one simple word. Commands that need free text
# (/tell, /remind, /todo, /new, /find, /tag) stay as they are.
TAPPABLE = ("plan", "build", "yes", "no", "always", "peek", "stop", "resume", "diff", "log", "undo", "deploy", "r",
            "save", "dismiss", "retry", "rewatch", "deeper", "done", "snooze", "failover")
EXTRA = (r"haiku|sonnet|opus|fable|codex|safe|normal|deep|local|full|on|off|tomorrow|yes|\d{1,3}[mhdw]")
TAP_RE = re.compile(rf"(?<![\w/])/({'|'.join(TAPPABLE)})((?:[ _](?:#?\d+|R\d+))(?:[ _](?:{EXTRA}))?|[ _](?:on|off))"
                    rf"(?![\w:/])", re.I)
PLACEHOLDER = "\x00{}\x00"


def tapify(text):
    """"/plan 4" -> "/plan_4": Telegram then links the whole command, number included."""
    return TAP_RE.sub(lambda m: "/" + m.group(1) + m.group(2).replace(" ", "_").replace("#", ""), text)


def command_words(text):
    """The reverse, for the bot reading a tapped command: "/snooze_R3_1h" -> "/snooze R3 1h"."""
    m = re.match(r"^/([a-z]+)_(\S+)(.*)$", text.strip(), re.I | re.S)
    if not m or m.group(1).lower() not in TAPPABLE:
        return text
    return f"/{m.group(1)} {m.group(2).replace('_', ' ')}{m.group(3)}"


def _inline(line):
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return PLACEHOLDER.format(len(codes) - 1)

    line = re.sub(r"`([^`\n]+)`", keep, line)
    links = []

    def link(m):
        links.append((m.group(1), m.group(2)))
        return PLACEHOLDER.format(f"L{len(links) - 1}")

    line = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", link, line)
    line = html.escape(line, quote=False)
    line = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", line)
    line = re.sub(r"(?<![\w*])\*(?=\S)([^*\n<>]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", line)  # never across a tag
    line = tapify(line)
    for i, (label, url) in enumerate(links):
        line = line.replace(PLACEHOLDER.format(f"L{i}"),
                            f'<a href="{html.escape(url)}">{html.escape(label, quote=False)}</a>')
    for i, code in enumerate(codes):
        line = line.replace(PLACEHOLDER.format(i), f"<code>{html.escape(code, quote=False)}</code>")
    return line


def to_html(text):
    """Light Markdown -> Telegram HTML (always balanced tags; unknown syntax is just escaped text)."""
    out, quote, fence = [], [], None

    def flush_quote():
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for raw in text.split("\n"):
        if fence is not None:
            if raw.strip().startswith("```"):
                out.append("<pre>" + html.escape("\n".join(fence), quote=False) + "</pre>")
                fence = None
            else:
                fence.append(raw)
            continue
        if raw.strip().startswith("```"):
            flush_quote()
            fence = []
            continue
        if re.match(r"^\s*>\s?", raw):
            quote.append(_inline(re.sub(r"^\s*>\s?", "", raw)))
            continue
        flush_quote()
        if re.match(r"^\s*\|?\s*:?-{3,}", raw) and "|" in raw:
            continue  # table separator row
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", raw):
            out.append("")
            continue
        m = re.match(r"^\s*#{1,6}\s+(.*)$", raw)
        if m:
            out.append("<b>" + _inline(m.group(1).strip("# ")) + "</b>")
            continue
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", raw)
        if m:
            out.append(" " * min(len(m.group(1)), 4) + "• " + _inline(m.group(2)))
            continue
        if raw.strip().startswith("|") and raw.strip().endswith("|"):
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            out.append(_inline(" · ".join(c for c in cells if c)))
            continue
        out.append(_inline(raw))
    if fence is not None:  # unclosed block: show what there is
        out.append("<pre>" + html.escape("\n".join(fence), quote=False) + "</pre>")
    flush_quote()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def plain(text):
    """Fallback if Telegram rejects the HTML: the text without markup, commands still tappable."""
    text = re.sub(r"```\w*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", text, flags=re.M)
    return tapify(text).strip()


def chunks(text, limit=3500):
    """Split long text on blank lines (never inside a ``` block) so each part converts and fits on its own."""
    parts, cur, in_fence = [], "", False
    for block in re.split(r"(\n\s*\n)", text):
        in_fence ^= block.count("```") % 2 == 1
        if cur and len(cur) + len(block) > limit and not in_fence:
            parts.append(cur)
            cur = ""
        cur += block
        while len(cur) > limit + 500:  # one huge paragraph: cut at a line break
            cut = cur.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            parts.append(cur[:cut])
            cur = cur[cut:]
    if cur.strip():
        parts.append(cur)
    return [p.strip("\n") for p in parts if p.strip()]
