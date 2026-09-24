// End-to-end tests of the Worker in workerd (Miniflare) with a real local D1 database and fake Telegram,
// Gemini and Claude routine servers. Run: npm test (in cloud/).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, beforeEach, test } from 'node:test';
import { Miniflare } from 'miniflare';
import { parseWhen, splitReminder, nextTime, sectionOf, pcToken, toHtml, tapify, commandWords, chunks } from './worker.mjs';

const BOT = '123:TEST';
const OWNER = 42;
const HOOK = 'https://reel.example.workers.dev/telegram';
const fake = { webhook: '', pending: 0, pendingAfter: null, sent: [], calls: [], fires: [] };

async function outbound(request) {
  const url = new URL(request.url);
  const body = request.method === 'GET' ? null : await request.clone().text();
  if (url.host === 'tg.test') {
    const method = url.pathname.split('/').pop();
    fake.calls.push(method);
    const params = body && body.startsWith('{') ? JSON.parse(body) : {};
    const ok = (result) => Response.json({ ok: true, result });
    if (url.pathname.startsWith('/file/')) return new Response(new Uint8Array([1, 2, 3, 4]));
    if (method === 'getWebhookInfo') {
      const info = { url: fake.webhook, pending_update_count: fake.pending };
      if (fake.pendingAfter !== null) { fake.pending = fake.pendingAfter; fake.pendingAfter = null; }
      if (fake.pendingSeq?.length) fake.pending = fake.pendingSeq.shift();
      return ok(info);
    }
    if (method === 'setWebhook') { fake.webhook = params.url; fake.webhookParams = params; return ok(true); }
    if (method === 'sendMessage') {
      if (params.parse_mode === 'HTML' && params.text.includes('FAILPARSE'))
        return Response.json({ ok: false, description: "Bad Request: can't parse entities: unexpected end tag" }, { status: 400 });
      fake.sent.push(params);
      return ok({ message_id: 1 });
    }
    if (method === 'sendDocument') { fake.sent.push({ document: true }); return ok({}); }
    if (method === 'getFile') return ok({ file_path: `videos/${params.file_id}.mp4`, file_size: 4 });
    return ok(true);
  }
  if (url.host === 'gem.test') {
    fake.calls.push(`gemini ${request.method} ${url.pathname}`);
    if (url.pathname === '/upload/v1beta/files') return new Response('{}', { headers: { 'x-goog-upload-url': 'https://gem.test/upload-here' } });
    if (url.pathname === '/upload-here') return Response.json({ file: { name: 'files/f1', uri: 'https://gem.test/files/f1', state: 'ACTIVE' } });
    if (url.pathname.includes(':generateContent')) {
      fake.geminiBody = JSON.parse(body);
      return Response.json({ candidates: [{ content: { parts: [{ text: '## Summary\nA neat MCP trick\n\n## Tools, links and repos\n- `some/repo`\n\n## Notes\nnone' }] } }] });
    }
    return Response.json({});
  }
  if (url.host === 'api.anthropic.com') {
    fake.fires.push(JSON.parse(JSON.parse(body).text));
    return Response.json({ claude_code_session_id: 'session_x', claude_code_session_url: 'https://claude.ai/code/session_x' });
  }
  return new Response('unexpected host ' + url.host, { status: 599 });
}

let mf, db, pc;
before(async () => {
  mf = new Miniflare({
    modules: true, scriptPath: new URL('./worker.mjs', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'),
    compatibilityDate: '2026-08-01', d1Databases: ['DB'], outboundService: outbound,
    bindings: {
      TELEGRAM_BOT_TOKEN: BOT, TELEGRAM_OWNER_ID: String(OWNER), TELEGRAM_WEBHOOK_SECRET: 'hook-secret',
      BACKEND_TOKEN: 'backend-secret', GEMINI_API_KEY: 'gem-key', PUBLIC_URL: 'https://reel.example.workers.dev',
      TELEGRAM_API: 'https://tg.test', GEMINI_API: 'https://gem.test', WATCHDOG_RECHECK_MS: '10', WATCHDOG_CONFIRM_MS: '10',
      ALBUM_SETTLE_MS: '0', GEMINI_POLL_MS: '1', GEMINI_RETRY_MS: '1',
      CLAUDE_ROUTINE_URL: 'https://api.anthropic.com/v1/claude_code/routines/trig_test/fire', CLAUDE_ROUTINE_TOKEN: 't',
    },
  });
  db = await mf.getD1Database('DB');
  pc = await pcToken(BOT);
});
after(() => mf.dispose());

beforeEach(async () => {
  const schema = readFileSync(new URL('./schema.sql', import.meta.url), 'utf8');
  await db.batch(['updates', 'jobs', 'runs', 'approvals', 'reminders', 'state'].map(t => db.prepare(`DROP TABLE IF EXISTS ${t}`)));
  const statements = schema.replace(/--.*$/gm, '').split(';').map(s => s.trim()).filter(Boolean);
  await db.batch(statements.map(s => db.prepare(s)));
  Object.assign(fake, { webhook: '', pending: 0, pendingAfter: null, pendingSeq: [], sent: [], calls: [], fires: [] });
});

let updateId = 1;
const telegram = (message) => mf.dispatchFetch('https://reel.example.workers.dev/telegram', {
  method: 'POST', headers: { 'x-telegram-bot-api-secret-token': 'hook-secret', 'content-type': 'application/json' },
  body: JSON.stringify({ update_id: updateId++, message: { message_id: updateId, chat: { id: OWNER, type: 'private' }, from: { id: OWNER }, ...message } }),
});
const pcCall = (path, body) => mf.dispatchFetch(`https://reel.example.workers.dev${path}`, {
  method: body ? 'POST' : 'GET', headers: { authorization: `Bearer ${pc}`, 'content-type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
});
const cron = async () => (await mf.getWorker()).scheduled({ cron: '* * * * *' });
const state = async (key) => (await db.prepare('SELECT value FROM state WHERE key=?').bind(key).first())?.value;
const lastText = () => fake.sent.at(-1)?.text || '';

// ------------------------------------------------------------ pure helpers
test('reminder times parse like remind.py', () => {
  const now = new Date(Date.UTC(2026, 8, 24, 14, 30)); // Thursday 14:30 owner time
  const fmt = (d) => d.toISOString().slice(0, 16).replace('T', ' ');
  assert.equal(fmt(parseWhen('tomorrow 9:00', now)), '2026-09-25 09:00');
  assert.equal(fmt(parseWhen('in 2h', now)), '2026-09-24 16:30');
  assert.equal(fmt(parseWhen('friday evening', now)), '2026-09-25 19:00');
  assert.equal(fmt(parseWhen('thursday 8am', now)), '2026-10-01 08:00');
  assert.equal(fmt(parseWhen('13:00', now)), '2026-09-25 13:00');
  assert.equal(fmt(parseWhen('2026-10-02 18:15', now)), '2026-10-02 18:15');
  assert.equal(parseWhen('call the bank', now), null);
  const split = splitReminder('every weekday 9:00 stand-up notes', now);
  assert.equal(split.every, 'weekday');
  assert.equal(split.what, 'stand-up notes');
  assert.equal(splitReminder('tomorrow 9:00 call the bank', now).what, 'call the bank');
  assert.equal(nextTime('2026-09-25 09:00', 'weekday', new Date(Date.UTC(2026, 8, 25, 10))), '2026-09-28 09:00');
  assert.equal(sectionOf('## Summary\nOne line\n\n## Notes\nx', 'Summary'), 'One line');
});

// ------------------------------------------------------------ access control
test('webhook, backend and PC endpoints reject wrong secrets', async () => {
  assert.equal((await mf.dispatchFetch('https://reel.example.workers.dev/health')).status, 200);
  const hook = await mf.dispatchFetch('https://reel.example.workers.dev/telegram', { method: 'POST', body: '{}', headers: { 'x-telegram-bot-api-secret-token': 'nope' } });
  assert.equal(hook.status, 403);
  const backend = await mf.dispatchFetch('https://reel.example.workers.dev/backend/run/x', { headers: { authorization: 'Bearer nope' } });
  assert.equal(backend.status, 403);
  const pcBad = await mf.dispatchFetch('https://reel.example.workers.dev/pc/status', { headers: { authorization: 'Bearer nope' } });
  assert.equal(pcBad.status, 403);
  assert.equal((await pcCall('/pc/status')).status, 200);
});

test('messages from anyone but the owner are ignored', async () => {
  await mf.dispatchFetch('https://reel.example.workers.dev/telegram', {
    method: 'POST', headers: { 'x-telegram-bot-api-secret-token': 'hook-secret' },
    body: JSON.stringify({ update_id: 999, message: { message_id: 1, chat: { id: 7, type: 'private' }, from: { id: 7 }, text: '/jobs' } }),
  });
  assert.equal(fake.sent.length, 0);
});

// ------------------------------------------------------------ failover
test('watchdog leaves the bot alone while the PC polls', async () => {
  fake.pending = 0;
  await cron();
  assert.ok(!fake.calls.includes('setWebhook'));
  fake.pending = 1; fake.pendingAfter = 0; // the PC collects it during the recheck
  await cron();
  assert.ok(!fake.calls.includes('setWebhook'));
});

test('watchdog takes over when updates sit unclaimed, and notices the PC coming back', async () => {
  fake.pending = 2;
  await cron();
  assert.equal(fake.webhook, HOOK);
  assert.equal(fake.webhookParams.secret_token, 'hook-secret');
  assert.equal(fake.webhookParams.drop_pending_updates, false);
  assert.equal(await state('mode'), 'cloud');
  assert.match(lastText(), /cloud took over/);
  assert.ok(fake.sent.some(m => /Got your message/.test(m.text) && !m.disable_notification), 'audible heads-up first');
  const notices = fake.sent.length;
  await cron(); // still ours: no second notice
  assert.equal(fake.sent.length, notices);
  fake.webhook = ''; fake.pending = 0; // the PC plugin deleted the webhook when it started
  await cron();
  assert.equal(await state('mode'), 'pc');
  assert.match(lastText(), /PC bot is back/);
});

test('/failover off stops the watchdog', async () => {
  await pcCall('/pc/command', { text: '/failover off' });
  fake.pending = 3;
  await cron();
  assert.equal(fake.webhook, '');
});

// ------------------------------------------------------------ shared library
test('PC sync sets job numbers; cloud jobs come back through /pc/changes and ack', async () => {
  const sync = await pcCall('/pc/sync', {
    tz_offset_min: 60, next_job: 6, next_reminder: 5,
    jobs: [{ id: 3, status: 'saved', summary: 'Old reel about MCP', source: 'https://instagram.com/reel/x', breakdown: '# notes', tags: 'mcp', created: '2026-09-20 10:00' }],
  });
  assert.equal(sync.status, 200);
  await telegram({ text: '/new build a tiny CLI' });
  assert.match(lastText(), /<b>Idea #6 saved<\/b>/);
  await telegram({ text: '/find mcp' });
  assert.match(lastText(), /<b>#3<\/b> Old reel about MCP · <i>saved<\/i>  \/r_3/);
  const changes = await (await pcCall('/pc/changes')).json();
  assert.deepEqual(changes.jobs.map(j => j.id), [6]);
  await pcCall('/pc/ack', { jobs: changes.jobs.map(j => ({ id: j.id, updated_at: j.updated_at })) });
  assert.equal((await (await pcCall('/pc/changes')).json()).jobs.length, 0);
  await telegram({ text: '/save 3' }); // a cloud change to a PC reel goes back to the PC too
  assert.deepEqual((await (await pcCall('/pc/changes')).json()).jobs.map(j => [j.id, j.status]), [[3, 'saved']]);
});

// ------------------------------------------------------------ watching
test('a Telegram video is watched by Gemini in the Worker, without a routine run', async () => {
  await pcCall('/pc/sync', { next_job: 10 });
  await telegram({ video: { file_id: 'vid1', mime_type: 'video/mp4' }, caption: 'look at this' });
  assert.match(lastText(), /#10 watching it in the cloud/);
  await cron();
  const item = await db.prepare('SELECT * FROM jobs WHERE id=10').first();
  assert.equal(item.status, 'done');
  assert.equal(item.summary, 'A neat MCP trick');
  assert.match(lastText(), /🎬 <b>#10 · A neat MCP trick<\/b>[\s\S]*<code>some\/repo<\/code>[\s\S]*\/plan_10  plan it/);
  assert.equal(fake.sent.at(-1).parse_mode, 'HTML');
  assert.equal(fake.fires.length, 0);
  assert.ok(fake.calls.includes('gemini DELETE /v1beta/files/f1'));
  assert.match(JSON.stringify(fake.geminiBody), /look at this/);
});

test('album photos become one job', async () => {
  await telegram({ photo: [{ file_id: 'p1small' }, { file_id: 'p1' }], media_group_id: 'g1' });
  await telegram({ photo: [{ file_id: 'p2' }], media_group_id: 'g1' });
  const jobs = await db.prepare('SELECT * FROM jobs').all();
  assert.equal(jobs.results.length, 1);
  assert.deepEqual(JSON.parse(jobs.results[0].source).files.map(f => f.file_id), ['p1', 'p2']);
  await cron();
  assert.match(JSON.stringify(fake.geminiBody), /social media post/);
});

test('Instagram links go to a routine; repeats are caught', async () => {
  await telegram({ text: 'https://www.instagram.com/reel/abc/' });
  assert.equal(fake.fires.length, 1);
  assert.equal(fake.fires[0].action, 'watch');
  await telegram({ text: 'https://www.instagram.com/reel/abc/' });
  assert.match(lastText(), /still being watched/);
});

test('/plan fires the routine with the run id', async () => {
  await telegram({ text: '/new idea' });
  await telegram({ text: '/plan 1' });
  assert.equal(fake.fires.at(-1).action, 'plan');
  assert.equal(fake.fires.at(-1).job_id, 1);
  assert.equal((await db.prepare('SELECT status FROM jobs WHERE id=1').first()).status, 'planning');
});

test('PC-only commands answer without spending a routine run', async () => {
  await telegram({ text: '/deploy 3' });
  assert.match(lastText(), /needs your PC/);
  assert.equal(fake.fires.length, 0);
});

// ------------------------------------------------------------ reminders
test('/remind is parsed in the Worker in the owner time zone', async () => {
  await pcCall('/pc/sync', { tz_offset_min: 60, next_reminder: 5 });
  await telegram({ text: '/remind in 1h call the bank' });
  assert.match(lastText(), /<b>R5 set<\/b> · \w{3} \d{1,2} \w{3}, \d\d:\d\d\ncall the bank/);
  const row = await db.prepare("SELECT * FROM reminders WHERE rid='R5'").first();
  assert.equal(Date.parse(row.due_utc) - Date.now() < 3700e3, true);
  assert.equal(row.changed, 1);
  assert.equal(fake.fires.length, 0);
  await telegram({ text: '/remind someday maybe' });
  assert.match(lastText(), /couldn’t read the time/);
});

test('each reminder occurrence is sent once, by the PC or by the cloud', async () => {
  const past = (min) => { const d = new Date(Date.now() + 60 * 60000 - min * 60000); return d.toISOString().slice(0, 16).replace('T', ' '); };
  await pcCall('/pc/sync', {
    tz_offset_min: 60, reminders: [
      { rid: 'R1', text: 'claimed by PC', due: past(10), status: 'open' },
      { rid: 'R2', text: 'PC missed it', due: past(10), status: 'open', every: 'day' },
      { rid: 'R3', text: 'just due', due: past(1), status: 'open' },
    ],
  });
  assert.deepEqual(await (await pcCall('/pc/reminder/claim', { rid: 'R1', due: past(10) })).json(), { send: true });
  assert.deepEqual(await (await pcCall('/pc/reminder/claim', { rid: 'R1', due: past(10) })).json(), { send: false });
  assert.deepEqual(await (await pcCall('/pc/reminder/claim', { rid: 'R9', due: past(1) })).json(), { send: true });
  await cron(); // PC mode: only R2 is past the grace period and unclaimed
  const texts = fake.sent.map(m => m.text).join('\n');
  assert.match(texts, /Reminder R2/);
  assert.doesNotMatch(texts, /Reminder R1|Reminder R3/);
  const r2 = await db.prepare("SELECT * FROM reminders WHERE rid='R2'").first();
  assert.ok(r2.due_local > past(0), 'repeating reminder moved to its next time');
  assert.equal(r2.changed, 1);
  // A PC push must not overwrite the cloud's newer copy, and done items disappear.
  await pcCall('/pc/sync', { reminders: [{ rid: 'R2', text: 'stale', due: past(10), status: 'open' }] });
  assert.equal((await db.prepare("SELECT text FROM reminders WHERE rid='R2'").first()).text, 'PC missed it');
  assert.equal(await db.prepare("SELECT rid FROM reminders WHERE rid='R1'").first(), null);
});

test('/done and /snooze in the cloud reach the PC as changes', async () => {
  await pcCall('/pc/sync', { tz_offset_min: 60, reminders: [{ rid: 'R4', text: 'water plants', due: '2030-01-01 09:00', status: 'open' }] });
  await telegram({ text: '/snooze R4 2h' });
  assert.match(lastText(), /<b>R4 snoozed<\/b> to/);
  await telegram({ text: '/done r4' });
  const changes = await (await pcCall('/pc/changes')).json();
  assert.deepEqual(changes.reminders.map(r => [r.rid, r.status]), [['R4', 'done']]);
});

// ------------------------------------------------------------ routine bridge
test('a routine finishing a job marks it for the PC to copy', async () => {
  await pcCall('/pc/sync', { jobs: [{ id: 2, status: 'done', summary: 'x', source: 'y' }] });
  const patch = await mf.dispatchFetch('https://reel.example.workers.dev/backend/job/2', {
    method: 'PATCH', headers: { authorization: 'Bearer backend-secret', 'content-type': 'application/json' },
    body: JSON.stringify({ plan: '# Plan', status: 'done' }),
  });
  assert.equal(patch.status, 200);
  assert.deepEqual((await (await pcCall('/pc/changes')).json()).jobs.map(j => [j.id, j.plan]), [[2, '# Plan']]);
});

test('forwarded /yes from the PC bot approves a cloud build command', async () => {
  await telegram({ text: '/new thing' });
  await db.prepare("INSERT INTO runs(id,job_id,action,chat_id,status,session_id) VALUES('r1',1,'build',42,'started','s1')").run();
  await db.prepare("INSERT INTO approvals(id,run_id,tool_use_id,command) VALUES('a1','r1','t1','npm test')").run();
  await pcCall('/pc/command', { text: '/yes 1' });
  assert.equal((await db.prepare("SELECT decision FROM approvals WHERE id='a1'").first()).decision, 'yes');
  assert.match(lastText(), /Allowed\. #1 carries on/);
});

test('a PC copy of a cloud job never replaces its source or live status', async () => {
  await telegram({ text: 'https://www.instagram.com/reel/zzz/' });
  const before = await db.prepare('SELECT source,status FROM jobs WHERE id=1').first();
  await pcCall('/pc/sync', { jobs: [{ id: 1, status: 'cloud-watching', summary: 's', source: 'https://www.instagram.com/reel/zzz/ (text)' }] });
  const after = await db.prepare('SELECT source,status FROM jobs WHERE id=1').first();
  assert.deepEqual(after, before);
});

// ------------------------------------------------------------ message formatting
test('formatter matches the PC side (tgfmt.py)', () => {
  assert.equal(tapify('/plan 5 · /build 5 · /save 5'), '/plan_5 · /build_5 · /save_5');
  assert.equal(tapify('/snooze R3 1h or /done R3'), '/snooze_R3_1h or /done_R3');
  assert.equal(tapify('/build 4 opus to redo'), '/build_4_opus to redo');
  for (const t of ['/tell 4 <changes>', '/remind tomorrow 9:00 call', '/plan N', 'see /telegram:access pair'])
    assert.equal(tapify(t), t);
  assert.equal(commandWords('/snooze_R3_1h'), '/snooze R3 1h');
  assert.equal(commandWords('/some_thing'), '/some_thing');
  const out = toHtml('# Title\n**Bold** and *it* and `a<b>`\n- one\n  - two\n> quoted\n[docs](https://x.dev/a?b=1&c=2)\n/plan 4');
  for (const part of ['<b>Title</b>', '<b>Bold</b>', '<i>it</i>', '<code>a&lt;b&gt;</code>', '• one\n  • two',
    '<blockquote>quoted</blockquote>', '<a href="https://x.dev/a?b=1&amp;c=2">docs</a>', '/plan_4'])
    assert.ok(out.includes(part), part);
  assert.equal(toHtml('**a *b** c*'), '<b>a *b</b> c*');
  assert.ok(toHtml('```\nnpm i **x** /plan 4\n```').startsWith('<pre>npm i **x** /plan 4</pre>'));
  const long = Array.from({ length: 40 }, (_, i) => `Paragraph ${i} ` + 'word '.repeat(60)).join('\n\n');
  assert.ok(chunks(long).length > 1 && chunks(long).every(c => toHtml(c).length < 4096));
});

test('a tapped command with underscores works like the typed one', async () => {
  await telegram({ text: '/new tiny CLI' });
  await telegram({ text: '/plan_1' });
  assert.equal(fake.fires.at(-1).action, 'plan');
  await telegram({ text: '/save_1' });
  assert.match(lastText(), /#1 saved for later/);
});

test('markup Telegram rejects is resent as plain text', async () => {
  await telegram({ text: '/new FAILPARSE **idea**' });
  assert.equal(fake.sent.at(-1).parse_mode, undefined);
  assert.match(lastText(), /Idea #1 saved[\s\S]*\/plan_1/);
});

test('a heads-up goes out as soon as a message is stuck; a late PC pickup gets a never-mind', async () => {
  fake.pending = 1; fake.pendingSeq = [1, 0]; // stuck at the confirm check, collected before the takeover
  await cron();
  assert.equal(fake.webhook, '');
  const texts = fake.sent.map(m => m.text).join(' | ');
  assert.match(texts, /Got your message/);
  assert.match(texts, /Never mind, your PC bot just picked it up/);
});
