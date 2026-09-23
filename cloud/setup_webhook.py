"""Register the single Telegram webhook after the Worker is deployed."""
import json
import os
import sys
import urllib.parse
import urllib.request


def main():
    worker = os.environ["REEL_CLOUD_URL"].rstrip("/")
    bot = os.environ["TELEGRAM_BOT_TOKEN"]
    secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
    if not worker.startswith("https://") or not secret or not bot:
        raise ValueError("A HTTPS Worker URL, bot token, and webhook secret are required")
    url = f"https://api.telegram.org/bot{bot}/setWebhook"
    data = urllib.parse.urlencode({
        "url": worker + "/telegram", "secret_token": secret,
        "max_connections": 1, "drop_pending_updates": "false",
    }).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if not body.get("ok"):
        raise RuntimeError(body.get("description", "Telegram rejected the webhook"))
    print("Telegram webhook registered at", worker + "/telegram")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"webhook setup failed: {exc}", file=sys.stderr)
        sys.exit(1)

