// Run: npm test   (uses temp folders; never touches your real Telegram state or reels)
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const cli = (args, env = {}, cwd = here) =>
  spawnSync(process.execPath, [path.join(here, "cli.mjs"), ...args], { cwd, encoding: "utf8", env: { ...process.env, ...env } });
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "reel-agent-test-"));

test("help and version", () => {
  const h = cli(["--help"]);
  assert.equal(h.status, 0);
  assert.match(h.stdout, /npx reel-agent watch <link>/);
  const v = cli(["--version"]);
  assert.equal(v.stdout.trim(), JSON.parse(fs.readFileSync(path.join(here, "package.json"), "utf8")).version);
});

test("unknown command fails", () => {
  assert.equal(cli(["nope"]).status, 1);
});

test("pair approves a pending code like /telegram:access pair + policy allowlist", () => {
  const state = tmp();
  const future = Date.now() + 600000;
  fs.writeFileSync(path.join(state, "access.json"), JSON.stringify({
    dmPolicy: "pairing", allowFrom: [], groups: {},
    pending: { abc123: { senderId: "42", chatId: "42", createdAt: Date.now(), expiresAt: future },
               old999: { senderId: "7", chatId: "7", createdAt: 0, expiresAt: 1 } },
  }));
  const env = { TELEGRAM_STATE_DIR: state };
  assert.equal(cli(["pair", "wrong1"], env).status, 1);
  assert.equal(cli(["pair", "old999"], env).status, 1, "expired codes are refused");
  assert.equal(cli(["pair", "abc123"], env).status, 0);
  const a = JSON.parse(fs.readFileSync(path.join(state, "access.json"), "utf8"));
  assert.deepEqual(a.allowFrom, ["42"]);
  assert.equal(a.dmPolicy, "allowlist");
  assert.ok(!a.pending.abc123);
  assert.equal(fs.readFileSync(path.join(state, "approved", "42"), "utf8"), "42");
});

test("watch runs the bundled watcher on a local video, output in the current folder", (t) => {
  const py = process.platform === "win32" ? "python" : "python3";
  const ff = spawnSync(py, ["-c", "import imageio_ffmpeg, yt_dlp; print(imageio_ffmpeg.get_ffmpeg_exe())"], { encoding: "utf8" });
  if (ff.status !== 0 || !fs.existsSync(path.join(here, "py", "reel.py"))) return t.skip("needs yt-dlp, imageio-ffmpeg and npm run prepack");
  const dir = tmp();
  spawnSync(ff.stdout.trim(), ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x284:rate=10",
    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "demo.mp4"], { cwd: dir });
  const env = { GEMINI_API_KEY: "", GOOGLE_API_KEY: "" };
  const r = cli(["watch", "demo.mp4", "--engine", "local", "--no-transcript", "--max-frames", "2"], env, dir);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /engine: local/);
  assert.ok(fs.readdirSync(path.join(dir, "reels")).length === 1);
});
