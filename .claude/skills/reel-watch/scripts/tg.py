"""tg.py - send a nicely formatted message to the user on Telegram (bold, code, one-tap commands).

Write light Markdown (see tgfmt.py): **bold**, `code`, "- " bullets, > quotes, and plain commands like "/plan 4"
(they become one tap). Prints "SENT <message_id>".

    python tg.py --chat <chat_id> [--reply-to <message_id>] "text"
    python tg.py --chat <chat_id> --file card.md
    some-command | python tg.py --chat <chat_id>          (text from stdin)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import tg_send, utf8_stdio  # noqa: E402


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*")
    ap.add_argument("--chat", help="chat id (default: the owner)")
    ap.add_argument("--reply-to", help="message id to reply to")
    ap.add_argument("--file", help="read the message from this file")
    a = ap.parse_args()
    if a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    elif a.text:
        text = " ".join(a.text)
    else:
        sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
    if not text.strip():
        sys.exit("nothing to send")
    sent = tg_send(text, a.chat, a.reply_to)
    if not sent:
        sys.exit("send failed")
    print(f"SENT {sent}")


if __name__ == "__main__":
    main()
