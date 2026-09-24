#!/usr/bin/env node
// reel-agent: one-command installer and controller for Reel Agent (https://github.com/HNF-FRN/Reel-watcher-telegram-Agent).
//
//   npx reel-agent                 install or update everything on Windows, start the bot, pair your phone
//   npx reel-agent watch <link>    any OS: have Gemini watch a video and print what it shows
//   npx reel-agent start|stop|restart|status|fix|update|tasks|quota
//   npx reel-agent pair [code]     approve your Telegram account
//   npx reel-agent doctor          check the whole setup, change nothing
//
// No dependencies: Node 18+ only. Every change to the PC is asked about first.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline/promises";
import { fileURLToPath } from "node:url";

const REPO = "https://github.com/HNF-FRN/Reel-watcher-telegram-Agent.git";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = JSON.parse(fs.readFileSync(path.join(HERE, "package.json"), "utf8"));
const IS_WIN = process.platform === "win32";
const HOME = os.homedir();
const SAVED = path.join(HOME, ".reel-agent.json");
const BOT_COMMANDS = ["start", "stop", "restart", "status", "fix", "update", "tasks", "quota", "autostart"];

const argv = process.argv.slice(2);
const flags = new Set(argv.filter((a) => a.startsWith("--") && !a.includes("=")));
const opt = (name) => {
  const i = argv.indexOf(`--${name}`);
  if (i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--")) return argv[i + 1];
  const eq = argv.find((a) => a.startsWith(`--${name}=`));
  return eq ? eq.slice(name.length + 3) : undefined;
};
const positional = argv.filter((a, i) => !a.startsWith("--") && !(i > 0 && argv[i - 1] === "--dir"));
const YES = flags.has("--yes") || flags.has("-y");

// ------------------------------------------------------------------ output
const tty = process.stdout.isTTY;
const paint = (code) => (s) => (tty ? `\x1b[${code}m${s}\x1b[0m` : s);
const bold = paint(1), dim = paint(2), green = paint(32), yellow = paint(33), red = paint(31), orange = paint("38;5;202");
const say = (s = "") => console.log(s);
const ok = (s) => say(`  ${green("✔")} ${s}`);
const warn = (s) => say(`  ${yellow("!")} ${s}`);
const fail = (s) => say(`  ${red("✖")} ${s}`);
const step = (n, s) => say(`\n${orange(bold(`${n}.`))} ${bold(s)}`);

async function ask(question, def = true) {
  if (YES) return def;
  if (!process.stdin.isTTY) return false;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    for (;;) {
      const a = (await rl.question(`  ${orange("?")} ${question} ${dim(def ? "[Y/n]" : "[y/N]")} `)).trim().toLowerCase();
      if (!a) return def;
      if (["y", "yes", "n", "no"].includes(a)) return a.startsWith("y");
    }
  } finally {
    rl.close();
  }
}

async function prompt(question) {
  if (!process.stdin.isTTY) return "";
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    return (await rl.question(`  ${orange("?")} ${question} `)).trim();
  } finally {
    rl.close();
  }
}

// ------------------------------------------------------------------ processes
function run(cmd, args, { cwd, env, inherit = false } = {}) {
  const r = spawnSync(cmd, args, {
    cwd, env: env || process.env, encoding: "utf8", windowsHide: !inherit,
    stdio: inherit ? "inherit" : ["ignore", "pipe", "pipe"],
  });
  return { code: r.error ? -1 : r.status, out: (r.stdout || "").trim(), err: (r.stderr || "").trim() };
}

function which(name) {
  const exts = IS_WIN ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";").map((e) => e.toLowerCase()) : [""];
  const dirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  if (IS_WIN) dirs.push(path.join(HOME, ".local", "bin"));
  for (const d of dirs) {
    for (const e of exts) {
      const p = path.join(d, name + e);
      // The Microsoft Store "python" stub opens the Store instead of running Python.
      if (fs.existsSync(p) && !/WindowsApps/i.test(p)) return p;
    }
  }
  return null;
}

// winget changes PATH in the registry; pick it up without asking the user to open a new window.
function refreshPath() {
  if (!IS_WIN) return;
  const r = run("powershell", ["-NoProfile", "-Command",
    "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"]);
  if (r.code === 0 && r.out) {
    const extra = [path.join(HOME, ".local", "bin"), path.join(process.env.APPDATA || "", "npm")];
    process.env.PATH = [r.out, ...extra, process.env.PATH].join(";");
  }
}

function findPython() {
  const candidates = IS_WIN ? [["python"], ["py", "-3"], ["python3"]] : [["python3"], ["python"]];
  for (const [cmd, ...pre] of candidates) {
    const exe = which(cmd);
    if (!exe) continue;
    const r = run(exe, [...pre, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
    const [maj, min] = (r.out || "0.0").split(".").map(Number);
    if (r.code === 0 && (maj > 3 || (maj === 3 && min >= 10))) return { exe, pre, version: `${maj}.${min}`, ok311: maj > 3 || min >= 11 };
  }
  return null;
}

// ------------------------------------------------------------------ install folder
function savedDir() {
  try { return JSON.parse(fs.readFileSync(SAVED, "utf8")).dir; } catch { return undefined; }
}
function isProject(d) {
  return !!d && fs.existsSync(path.join(d, "bot.ps1")) && fs.existsSync(path.join(d, "setup.py"));
}
function projectDir() {
  for (const d of [opt("dir"), process.env.REEL_AGENT_DIR, savedDir(), process.cwd(), path.join(HOME, "reel-agent")]) {
    if (d && isProject(path.resolve(d))) return path.resolve(d);
  }
  return null;
}

// ------------------------------------------------------------------ Telegram pairing
const stateDir = () => process.env.TELEGRAM_STATE_DIR
  || path.join(process.env.CLAUDE_CONFIG_DIR || path.join(HOME, ".claude"), "channels", "telegram");

function readAccess() {
  try {
    return JSON.parse(fs.readFileSync(path.join(stateDir(), "access.json"), "utf8"));
  } catch {
    return { dmPolicy: "pairing", allowFrom: [], groups: {}, pending: {} };
  }
}
function writeAccess(a) {
  const f = path.join(stateDir(), "access.json");
  fs.mkdirSync(stateDir(), { recursive: true });
  fs.writeFileSync(`${f}.tmp`, JSON.stringify(a, null, 2) + "\n");
  fs.renameSync(`${f}.tmp`, f);
}
const livePending = (a) => Object.entries(a.pending || {}).filter(([, p]) => !p.expiresAt || p.expiresAt > Date.now());

// Same steps as the Telegram plugin's own "/telegram:access pair <code>" and "policy allowlist".
function approve(code) {
  const a = readAccess();
  const p = (a.pending || {})[code];
  if (!p || (p.expiresAt && p.expiresAt < Date.now())) return null;
  a.allowFrom = [...new Set([...(a.allowFrom || []), String(p.senderId)])];
  delete a.pending[code];
  a.dmPolicy = "allowlist";
  writeAccess(a);
  const dir = path.join(stateDir(), "approved");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, String(p.senderId)), String(p.chatId));
  return p;
}

async function pair(code) {
  step("✓", "Pair your phone");
  if (code) {
    const p = approve(code.trim());
    if (p) ok(`Approved Telegram account ${p.senderId}. Only this account can use the bot now.`);
    else fail(`No pending pairing with code ${code}. Message your bot again for a fresh code.`);
    return !!p;
  }
  say("  Open Telegram on your phone and send any message to your bot.");
  say(dim("  Waiting for it to reply with a pairing code… (Ctrl+C to stop)"));
  const deadline = Date.now() + 10 * 60 * 1000;
  let seen = false;
  while (Date.now() < deadline) {
    if (livePending(readAccess()).length) { seen = true; break; }
    await new Promise((r) => setTimeout(r, 2000));
  }
  if (!seen) {
    warn("No message arrived. Run `npx reel-agent pair` when the bot is running.");
    return false;
  }
  for (let tries = 0; tries < 3; tries++) {
    const typed = (await prompt("Type the code the bot sent you:")).replace(/\s/g, "");
    if (!typed) break;
    const p = approve(typed);
    if (p) {
      ok(`Paired. Only your account (${p.senderId}) can use the bot, and strangers get no reply.`);
      return true;
    }
    fail("That code doesn't match. Check the bot's latest message.");
  }
  warn("Not paired. Run `npx reel-agent pair <code>` later.");
  return false;
}

// ------------------------------------------------------------------ commands
async function install() {
  say(`\n${bold("Reel Agent")} ${dim(`v${PKG.version}`)}  ${orange("·")}  send a reel, get it built\n`);
  if (!IS_WIN) {
    say("The full Telegram bot runs on Windows for now (macOS and Linux: github.com/HNF-FRN/Reel-watcher-telegram-Agent/issues/13).");
    say("\nOn this computer you can already:");
    say(`  ${bold("npx reel-agent watch <link>")}   have Gemini watch any reel, TikTok or YouTube video`);
    say(`  ${bold("In Claude Code:")}  /plugin marketplace add HNF-FRN/Reel-watcher-telegram-Agent`);
    say(`                   /plugin install reel-watch@reel-agent`);
    return 0;
  }

  step(1, "Tools");
  const winget = which("winget");
  const getWith = async (label, id) => {
    if (!winget) { fail(`${label} missing. Install it from its website, then run npx reel-agent again.`); return false; }
    if (!(await ask(`${label} is missing. Install it with winget (${id})?`))) return false;
    const r = run(winget, ["install", "--id", id, "-e", "--source", "winget"], { inherit: true });
    refreshPath();
    return r.code === 0;
  };

  let py = findPython();
  if (!py || !py.ok311) {
    if (py) warn(`Python ${py.version} is too old (need 3.11+).`);
    await getWith("Python 3.13", "Python.Python.3.13");
    py = findPython();
  }
  if (py && py.ok311) ok(`Python ${py.version}`);
  else { fail("Python 3.11+ is needed. Install it, open a new terminal and run npx reel-agent again."); return 1; }

  if (!which("git")) await getWith("Git", "Git.Git");
  if (which("git")) ok("Git");
  else { fail("Git is needed (every build is its own git repository)."); return 1; }

  if (!which("claude")) {
    if (await ask("Claude Code is missing. Install it now with Anthropic's official installer (claude.ai/install.ps1)?")) {
      run("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://claude.ai/install.ps1 | iex"], { inherit: true });
      refreshPath();
    }
  }
  const claude = which("claude");
  if (!claude) { fail("Claude Code is needed: https://claude.ai/code"); return 1; }
  ok("Claude Code");
  let auth = {};
  try { auth = JSON.parse(run(claude, ["auth", "status", "--json"]).out || "{}"); } catch { /* older Claude Code */ }
  if (auth.loggedIn === false) {
    say("  Claude Code isn't signed in yet. A browser window will open: sign in with your Claude Pro or Max account.");
    if (await ask("Sign in now?")) run(claude, ["auth", "login"], { inherit: true });
  } else if (auth.loggedIn) {
    ok(`Signed in${auth.subscriptionType ? ` (${auth.subscriptionType})` : ""}`);
  }

  step(2, "Get Reel Agent");
  let dir = projectDir();
  if (dir) {
    ok(`Found it in ${dir}`);
    const r = run("git", ["pull", "--ff-only"], { cwd: dir });
    if (r.code === 0) ok(r.out.includes("Already up to date") ? "Up to date" : "Updated to the latest version");
    else warn(`Couldn't update (${(r.err.split("\n").pop() || "git pull failed")}). Continuing with the current version.`);
  } else {
    dir = path.resolve(opt("dir") || path.join(HOME, "reel-agent"));
    if (fs.existsSync(dir) && fs.readdirSync(dir).length) {
      fail(`${dir} already exists and isn't Reel Agent. Pick another folder: npx reel-agent --dir <folder>`);
      return 1;
    }
    if (!(await ask(`Download Reel Agent into ${dir}?`))) { say("  Nothing was changed."); return 1; }
    const r = run("git", ["clone", "--depth", "1", REPO, dir], { inherit: true });
    if (r.code !== 0) { fail("Download failed. Check your internet connection and try again."); return 1; }
    ok(`Downloaded to ${dir}`);
  }
  fs.writeFileSync(SAVED, JSON.stringify({ dir }, null, 2));

  step(3, "Guided setup (Telegram bot token, Gemini key, packages, reminders, autostart)");
  say(dim("  Each step explains what it will change and asks first. Finished steps are skipped."));
  const s = run(py.exe, [...py.pre, "setup.py"], { cwd: dir, inherit: true });
  if (s.code !== 0) { fail("Setup stopped. Fix what it says, then run npx reel-agent again."); return 1; }

  if (!fs.existsSync(path.join(stateDir(), ".env"))) {
    warn("No bot token saved yet, so the bot can't start. Run npx reel-agent again once you have one from @BotFather.");
    return 1;
  }

  step(4, "Start the bot");
  if (await ask("Start the bot now? (it opens in its own window; keep that window open)")) {
    run("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(dir, "bot.ps1"), "start"], { cwd: dir, inherit: true });
    const a = readAccess();
    if ((a.allowFrom || []).length && a.dmPolicy === "allowlist") ok("Your phone is already paired.");
    else await pair();
  } else {
    say(`  Later: ${bold("npx reel-agent start")}, then ${bold("npx reel-agent pair")}.`);
  }

  say(`\n${green(bold("All set."))} Send ${bold("/menu")} to your bot, then share a reel with it.`);
  say(dim(`  Manage it from any terminal: npx reel-agent status | start | stop | update | doctor`));
  say(dim(`  Enjoying it? A star helps others find it: https://github.com/HNF-FRN/Reel-watcher-telegram-Agent`));
  return 0;
}

function botCommand(cmd, rest) {
  if (!IS_WIN) { fail("The bot runs on Windows only. Try: npx reel-agent watch <link>"); return 1; }
  const dir = projectDir();
  if (!dir) { fail("Reel Agent isn't installed yet. Run: npx reel-agent"); return 1; }
  return run("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(dir, "bot.ps1"), cmd, ...rest], { cwd: dir, inherit: true }).code;
}

function doctor() {
  const dir = projectDir();
  const py = findPython();
  if (!dir) { fail("Reel Agent isn't installed yet. Run: npx reel-agent"); return 1; }
  if (!py) { fail("Python not found."); return 1; }
  return run(py.exe, [...py.pre, "setup.py", "--check"], { cwd: dir, inherit: true }).code;
}

async function watch(sources) {
  if (!sources.length) { fail("Usage: npx reel-agent watch <link-or-video-file> [more images…]"); return 1; }
  const py = findPython();
  if (!py) { fail(`Python 3.10+ is needed: ${IS_WIN ? "winget install Python.Python.3.13" : "https://www.python.org/downloads/"}`); return 1; }
  const missing = ["yt_dlp", "imageio_ffmpeg"].filter((m) => run(py.exe, [...py.pre, "-c", `import ${m}`]).code !== 0);
  if (missing.length) {
    if (await ask(`Install the video downloader and ffmpeg (pip install yt-dlp imageio-ffmpeg)?`)) {
      run(py.exe, [...py.pre, "-m", "pip", "install", "--user", "yt-dlp", "imageio-ffmpeg"], { inherit: true });
    } else { fail("Needed: pip install yt-dlp imageio-ffmpeg"); return 1; }
  }
  if (!process.env.GEMINI_API_KEY && !process.env.GOOGLE_API_KEY && !fs.existsSync(path.resolve(".env"))) {
    say(dim("  Tip: set GEMINI_API_KEY (free at aistudio.google.com/apikey) so Gemini watches it with sound."));
    say(dim("  Without it you get frames and a transcript (pip install faster-whisper) instead of a breakdown.\n"));
  }
  const rest = argv.slice(argv.indexOf("watch") + 1);
  return run(py.exe, [...py.pre, path.join(HERE, "py", "reel.py"), ...rest], {
    inherit: true, env: { ...process.env, REEL_HOME: process.env.REEL_HOME || process.cwd(), PYTHONUTF8: "1" },
  }).code;
}

function help() {
  say(`${bold("reel-agent")} ${dim(`v${PKG.version}`)}  ·  send a reel to Telegram, get it explained and built on your PC

${bold("Setup (Windows)")}
  npx reel-agent                 install or update everything, start the bot, pair your phone
  npx reel-agent --dir <folder>  install somewhere other than ~/reel-agent

${bold("Any OS")}
  npx reel-agent watch <link>    Gemini watches a reel, TikTok, YouTube or X video and says what it shows

${bold("Manage the bot")}
  npx reel-agent status | start | stop | restart | fix | update | tasks | quota
  npx reel-agent pair [code]     approve your Telegram account
  npx reel-agent doctor          check the whole setup without changing anything

${dim("Docs: https://github.com/HNF-FRN/Reel-watcher-telegram-Agent")}`);
  return 0;
}

// ------------------------------------------------------------------ main
const [cmd = "install", ...rest] = positional;
let code;
try {
  if (flags.has("--help") || flags.has("-h") || cmd === "help") code = help();
  else if (flags.has("--version") || cmd === "version") { say(PKG.version); code = 0; }
  else if (cmd === "install" || cmd === "setup") code = await install();
  else if (cmd === "watch") code = await watch(rest);
  else if (cmd === "pair") code = (await pair(rest[0])) ? 0 : 1;
  else if (cmd === "doctor" || cmd === "check") code = doctor();
  else if (BOT_COMMANDS.includes(cmd)) code = botCommand(cmd, rest);
  else { fail(`Unknown command: ${cmd}`); help(); code = 1; }
} catch (e) {
  if (e && e.code === "ABORT_ERR") code = 130;
  else { fail(e && e.message ? e.message : String(e)); code = 1; }
}
process.exit(code ?? 0);
