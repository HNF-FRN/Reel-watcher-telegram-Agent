"""Check that setup saves the bot token in a form the Telegram plugin can read (docs/fix-no-pairing-code.md).
Run: python test_setup.py   (uses a temp folder, never touches your real token)"""
import re
import tempfile
from pathlib import Path
from unittest import mock

import setup


def plugin_token(env):
    # Same as the plugin's server.ts: split on "\n", match /^(\w+)=(.*)$/ (JS "." doesn't match "\r")
    for line in env.read_bytes().decode("utf-8").split("\n"):
        m = re.fullmatch(r"(\w+)=([^\r\n]*)", line)
        if m and m.group(1) == "TELEGRAM_BOT_TOKEN":
            return m.group(2)


def run_setup(tg_dir, check=False, pasted=""):
    fake = mock.Mock(stdout=f"{setup.PLUGIN}\n  Status: disabled", returncode=0)
    with mock.patch.object(setup, "TG_DIR", tg_dir), mock.patch.object(setup, "CHECK", check), \
         mock.patch.object(setup, "run", return_value=fake), mock.patch("shutil.which", return_value="x"), \
         mock.patch("getpass.getpass", return_value=pasted):
        setup.telegram()


TOKEN = "123456789:AAHabcdefghijklmnopqrstuvwxyz0123456"
with tempfile.TemporaryDirectory() as d:
    tg = Path(d) / "telegram"
    env = tg / ".env"

    run_setup(tg, pasted=f"  {TOKEN} ")                      # new setup: token pasted
    assert plugin_token(env) == TOKEN, env.read_bytes()

    for bad in (f"TELEGRAM_BOT_TOKEN={TOKEN}\r\n", f"﻿TELEGRAM_BOT_TOKEN={TOKEN}\r\n"):  # Notepad / old setup
        env.write_bytes(bad.encode("utf-8"))
        run_setup(tg, check=True)
        assert plugin_token(env) != TOKEN, "--check must not change the file"
        run_setup(tg)
        assert plugin_token(env) == TOKEN, env.read_bytes()
print("ok: setup saves a token the plugin can read")
