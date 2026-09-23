// Telegram's single webhook stays here. Claude cloud routines do the slow work.
const json = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'content-type': 'application/json; charset=utf-8' },
});
const text = (value, status = 200) => new Response(value, { status });
const token = (request) => request.headers.get('authorization')?.replace(/^Bearer /, '');
const owner = (env, message) => String(message?.from?.id) === String(env.TELEGRAM_OWNER_ID)
  && message?.chat?.type === 'private';
const safeInt = (value) => /^\d+$/.test(String(value || '')) ? Number(value) : 0;
const now = () => new Date().toISOString();
const reply = async (env, chat, body) => {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: chat, text: String(body).slice(0, 4000), disable_web_page_preview: true }),
  });
  if (!response.ok) throw new Error(`Telegram sendMessage failed: ${response.status}`);
};
const job = (env, id) => env.DB.prepare('SELECT * FROM jobs WHERE id=?').bind(id).first();
const run = (env, id) => env.DB.prepare('SELECT * FROM runs WHERE id=?').bind(id).first();
const fieldList = new Set(['status', 'summary', 'breakdown', 'plan', 'branch_url', 'result']);
const help = '🎬 Send a reel, video, screenshot, or /new idea\n/jobs · /r N · /retry N · /plan N · /build N\n/pending · /yes N · /no N · /tasks · /peek N · /stop N · /resume N\n/save N · /dismiss N · /find words · /remind when what · /reminders\nCloud builds create a GitHub branch for review.';

async function startRun(env, action, jobId, chatId, message = '') {
  const id = crypto.randomUUID();
  await env.DB.prepare('INSERT INTO runs(id,job_id,action,chat_id,message) VALUES(?,?,?,?,?)')
    .bind(id, jobId || null, action, chatId, message).run();
  await dispatchRun(env, id);
  return id;
}

async function dispatchRun(env, id) {
  const claimed = await env.DB.prepare("UPDATE runs SET status='dispatching',updated_at=? WHERE id=? AND status='queued'")
    .bind(now(), id).run();
  if (!claimed.meta.changes) return;
  const item = await run(env, id);
  const { action, job_id: jobId, chat_id: chatId, message } = item;
  try {
    const routineUrl = env.CLAUDE_ROUTINE_URL;
    if (!/^https:\/\/api\.anthropic\.com\/v1\/claude_code\/routines\/[^/]+\/fire$/.test(routineUrl))
      throw new Error('CLAUDE_ROUTINE_URL is not configured');
    const response = await fetch(routineUrl, {
      method: 'POST', headers: {
        authorization: `Bearer ${env.CLAUDE_ROUTINE_TOKEN}`,
        'anthropic-beta': 'experimental-cc-routine-2026-04-01',
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ text: JSON.stringify({ source: 'reel-agent-cloud', run_id: id, action, job_id: jobId, message }) }),
    });
    if (!response.ok) throw new Error(`Claude routine failed: ${response.status}`);
    const payload = await response.json();
    await env.DB.prepare("UPDATE runs SET status='started',session_id=COALESCE(?,session_id),session_url=?,updated_at=? WHERE id=? AND status='dispatching'")
      .bind(payload.claude_code_session_id || null, payload.claude_code_session_url || '', now(), id).run();
    if (jobId) await env.DB.prepare('UPDATE jobs SET status=?,updated_at=? WHERE id=?')
      .bind(action === 'watch' ? 'watching' : action === 'plan' ? 'planning' : 'building', now(), jobId).run();
  } catch (error) {
    await env.DB.prepare("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?")
      .bind(String(error), now(), id).run();
    if (jobId) await env.DB.prepare("UPDATE jobs SET status='failed',updated_at=? WHERE id=?")
      .bind(now(), jobId).run();
    await reply(env, chatId, `Cloud task could not start: ${error.message || error}`);
  }
}

function sourceOf(message) {
  const files = [message.video, message.document, message.animation, message.photo?.at(-1)].filter(Boolean);
  if (files.length) return JSON.stringify({ file_id: files[0].file_id, kind: message.photo ? 'photo' : 'video', name: files[0].file_name || '' });
  const url = (message.text || message.caption || '').match(/https?:\/\/\S+/i)?.[0];
  return url ? JSON.stringify({ url }) : '';
}

async function handleTelegram(request, env, ctx) {
  if (!env.TELEGRAM_WEBHOOK_SECRET || request.headers.get('x-telegram-bot-api-secret-token') !== env.TELEGRAM_WEBHOOK_SECRET)
    return text('forbidden', 403);
  const update = await request.json();
  if (!Number.isInteger(update.update_id)) return text('bad update', 400);
  const inserted = await env.DB.prepare('INSERT OR IGNORE INTO updates(update_id) VALUES(?)').bind(update.update_id).run();
  if (!inserted.meta.changes) return text('ok');
  const message = update.message;
  if (!message || !owner(env, message)) return text('ok');
  const chat = message.chat.id;
  const raw = (message.text || message.caption || '').trim();
  const match = raw.match(/^\/(\w+)(?:@\w+)?(?:\s+([\s\S]*))?$/);
  const cmd = match?.[1]?.toLowerCase();
  const arg = (match?.[2] || '').trim();
  try {
    if (cmd === 'start' || cmd === 'help' || cmd === 'menu') await reply(env, chat, help);
    else if (cmd === 'jobs' || cmd === 'saved') {
      const rows = await env.DB.prepare(`SELECT id,status,summary FROM jobs ${cmd === 'saved' ? "WHERE status='saved'" : ''} ORDER BY id DESC LIMIT 15`).all();
      await reply(env, chat, rows.results.length ? rows.results.map(x => `#${x.id} ${x.status} ${x.summary}`).join('\n') : 'No reels yet.');
    } else if (cmd === 'r') {
      const item = await job(env, safeInt(arg.split(/\s+/)[0]));
      await reply(env, chat, item ? `#${item.id} ${item.summary}\n\n${item.breakdown || item.result || item.status}` : 'Reel not found.');
    } else if (cmd === 'new') {
      if (!arg) await reply(env, chat, 'Use /new <idea>.');
      else {
        const result = await env.DB.prepare("INSERT INTO jobs(chat_id,source,note,status,summary) VALUES(?,?,?,'idea',?)")
          .bind(chat, JSON.stringify({ idea: arg }), arg, arg.slice(0, 180)).run();
        await reply(env, chat, `#${result.meta.last_row_id} saved. /plan ${result.meta.last_row_id} or /build ${result.meta.last_row_id}`);
      }
    } else if (cmd === 'plan' || cmd === 'build' || cmd === 'resume' || cmd === 'retry' || cmd === 'rewatch') {
      const n = safeInt(arg.split(/\s+/)[0]);
      const item = await job(env, n);
      if (!item) await reply(env, chat, 'Reel not found. Use /jobs.');
      else {
        const action = ['retry', 'rewatch'].includes(cmd) ? 'watch' : cmd === 'resume' ? 'build' : cmd;
        ctx.waitUntil(startRun(env, action, n, chat, arg.slice(String(n).length).trim()));
        await reply(env, chat, `${action === 'plan' ? '📋 Planning' : action === 'watch' ? '🎬 Watching' : '🛠 Building'} #${n} in the cloud…`);
      }
    } else if (cmd === 'pending' || cmd === 'tasks') {
      const rows = await env.DB.prepare("SELECT r.id,r.job_id,r.action,r.status FROM runs r WHERE r.status IN ('queued','started') ORDER BY created_at DESC LIMIT 15").all();
      const pending = await env.DB.prepare("SELECT a.id,r.job_id,a.command FROM approvals a JOIN runs r ON a.run_id=r.id WHERE a.decision='pending' ORDER BY a.created_at LIMIT 10").all();
      await reply(env, chat, [rows.results.map(x => `#${x.job_id || '?'} ${x.action}: ${x.status}`).join('\n'), pending.results.map(x => `🔐 #${x.job_id} ${x.command.slice(0, 140)}`).join('\n')].filter(Boolean).join('\n') || 'Nothing running or waiting.');
    } else if (cmd === 'yes' || cmd === 'no') {
      const n = safeInt(arg.split(/\s+/)[0]);
      const pending = await env.DB.prepare("SELECT a.id FROM approvals a JOIN runs r ON a.run_id=r.id WHERE r.job_id=? AND r.status='started' AND a.decision='pending' ORDER BY a.created_at LIMIT 1").bind(n).first();
      if (!pending) await reply(env, chat, `Nothing waiting for #${n}.`);
      else {
        await env.DB.prepare("UPDATE approvals SET decision=?,decided_at=? WHERE id=? AND decision='pending'")
          .bind(cmd, now(), pending.id).run();
        await reply(env, chat, `${cmd === 'yes' ? 'Approved' : 'Denied'} one command for #${n}.`);
      }
    } else if (cmd === 'peek' || cmd === 'log' || cmd === 'diff') {
      const n = safeInt(arg.split(/\s+/)[0]);
      const item = await job(env, n);
      const latest = await env.DB.prepare('SELECT * FROM runs WHERE job_id=? ORDER BY created_at DESC LIMIT 1').bind(n).first();
      await reply(env, chat, item ? `#${n} ${item.status}\n${item.result || item.summary}\n${item.branch_url || latest?.session_url || ''}` : 'Reel not found.');
    } else if (cmd === 'save' || cmd === 'dismiss') {
      const n = safeInt(arg.split(/\s+/)[0]);
      await env.DB.prepare('UPDATE jobs SET status=?,updated_at=? WHERE id=?').bind(cmd === 'save' ? 'saved' : 'dismissed', now(), n).run();
      await reply(env, chat, `#${n} ${cmd === 'save' ? 'saved' : 'dismissed'}.`);
    } else if (cmd === 'find') {
      const rows = await env.DB.prepare('SELECT id,summary FROM jobs WHERE summary LIKE ? OR breakdown LIKE ? ORDER BY id DESC LIMIT 10')
        .bind(`%${arg}%`, `%${arg}%`).all();
      await reply(env, chat, rows.results.map(x => `#${x.id} ${x.summary}`).join('\n') || 'No matches.');
    } else if (cmd === 'reminders') {
      const rows = await env.DB.prepare("SELECT id,text,due_at FROM reminders WHERE status='open' ORDER BY due_at LIMIT 15").all();
      await reply(env, chat, rows.results.map(x => `R${x.id} ${x.due_at} ${x.text}`).join('\n') || 'No reminders.');
    } else if (cmd === 'remind') {
      if (!arg) await reply(env, chat, 'Use /remind <when> <what>.');
      else {
        ctx.waitUntil(startRun(env, 'remind', null, chat, arg));
        await reply(env, chat, '⏰ Setting your reminder in the cloud…');
      }
    } else if (cmd === 'stop') {
      const n = safeInt(arg.split(/\s+/)[0]);
      await env.DB.prepare("UPDATE runs SET status='stop-requested',updated_at=? WHERE job_id=? AND status IN ('queued','started')")
        .bind(now(), n).run();
      await env.DB.prepare("UPDATE approvals SET decision='no',decided_at=? WHERE decision='pending' AND run_id IN (SELECT id FROM runs WHERE job_id=? AND status='stop-requested')")
        .bind(now(), n).run();
      await reply(env, chat, `Stop requested for #${n}. The cloud session will stop at its next check.`);
    } else if (cmd === 'undo' || cmd === 'deploy') {
      const [nText, confirmation] = arg.split(/\s+/);
      const n = safeInt(nText);
      if (!await job(env, n)) await reply(env, chat, 'Reel not found.');
      else if (confirmation !== 'yes') await reply(env, chat, `Confirm with /${cmd} ${n} yes. Cloud builds cannot change your powered-off PC; deploy acts on cloud targets only.`);
      else await reply(env, chat, `Cloud ${cmd} requires manual review of #${n}'s GitHub branch or pull request. No change was made.`);
    } else if (cmd) {
      ctx.waitUntil(startRun(env, 'command', null, chat, raw));
      await reply(env, chat, 'I’m handling that in the cloud…');
    } else {
      const source = sourceOf(message);
      if (!source) {
        ctx.waitUntil(startRun(env, 'command', null, chat, raw));
        await reply(env, chat, 'I’m handling that in the cloud…');
      } else {
        const result = await env.DB.prepare('INSERT INTO jobs(chat_id,source,note) VALUES(?,?,?)')
          .bind(chat, source, raw.slice(0, 1000)).run();
        const n = result.meta.last_row_id;
        ctx.waitUntil(startRun(env, 'watch', n, chat));
        await reply(env, chat, `#${n} watching it in the cloud…`);
      }
    }
  } catch (error) {
    await reply(env, chat, `I could not process that message: ${error.message || error}`);
  }
  return text('ok');
}

async function handleBackend(request, env, path) {
  if (!env.BACKEND_TOKEN || token(request) !== env.BACKEND_TOKEN) return text('forbidden', 403);
  const parts = path.split('/').filter(Boolean);
  if (parts[1] === 'run' && parts[2] && request.method === 'GET') {
    const value = await run(env, parts[2]);
    return value ? json(value) : text('not found', 404);
  }
  if (parts[1] === 'job' && parts[2] && request.method === 'GET') {
    const value = await job(env, safeInt(parts[2]));
    return value ? json(value) : text('not found', 404);
  }
  if (parts[1] === 'session' && parts[2] && request.method === 'GET') {
    const value = await env.DB.prepare('SELECT * FROM runs WHERE session_id=?').bind(parts[2]).first();
    return value ? json(value) : text('not found', 404);
  }
  if (parts[1] === 'file' && request.method === 'GET') {
    const fileId = new URL(request.url).searchParams.get('file_id');
    if (!fileId || fileId.length > 300) return text('bad file', 400);
    const metaResponse = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getFile?file_id=${encodeURIComponent(fileId)}`);
    if (!metaResponse.ok) return text('Telegram file unavailable', 502);
    const meta = await metaResponse.json();
    if (!meta.ok || !meta.result?.file_path) return text('Telegram file unavailable', 404);
    const fileResponse = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${meta.result.file_path}`);
    if (!fileResponse.ok) return text('Telegram download failed', 502);
    return new Response(fileResponse.body, { headers: {
      'content-type': fileResponse.headers.get('content-type') || 'application/octet-stream',
    } });
  }
  if (parts[1] === 'session' && request.method === 'POST') {
    const { run_id, session_id } = await request.json();
    if (!session_id || !await run(env, run_id)) return text('bad session', 400);
    await env.DB.prepare("UPDATE runs SET session_id=?,status='started',updated_at=? WHERE id=?")
      .bind(session_id, now(), run_id).run();
    return json({ ok: true });
  }
  if (parts[1] === 'run' && parts[2] && request.method === 'PATCH') {
    const value = await run(env, parts[2]);
    if (!value) return text('not found', 404);
    const { status, error } = await request.json();
    if (!['done', 'failed', 'stop-requested'].includes(status)) return text('bad status', 400);
    await env.DB.prepare('UPDATE runs SET status=?,error=?,updated_at=? WHERE id=?')
      .bind(status, String(error || '').slice(0, 1000), now(), value.id).run();
    if (status !== 'stop-requested') await env.DB.prepare("UPDATE approvals SET decision='no',decided_at=? WHERE run_id=? AND decision='pending'")
      .bind(now(), value.id).run();
    return json({ ok: true });
  }
  if (parts[1] === 'job' && parts[2] && request.method === 'PATCH') {
    const id = safeInt(parts[2]);
    const current = await job(env, id);
    if (!current) return text('not found', 404);
    const body = await request.json();
    const fields = Object.keys(body).filter(x => fieldList.has(x));
    if (!fields.length) return text('bad fields', 400);
    const values = fields.map(x => String(body[x]).slice(0, x === 'breakdown' || x === 'plan' ? 60000 : 4000));
    await env.DB.prepare(`UPDATE jobs SET ${fields.map(x => `${x}=?`).join(',')},updated_at=? WHERE id=?`)
      .bind(...values, now(), id).run();
    return json({ ok: true });
  }
  if (parts[1] === 'message' && request.method === 'POST') {
    const { chat_id, text: body } = await request.json();
    if (String(chat_id) !== String(env.TELEGRAM_OWNER_ID)) return text('forbidden', 403);
    await reply(env, chat_id, body);
    return json({ ok: true });
  }
  if (parts[1] === 'approval' && request.method === 'POST') {
    const { session_id, tool_use_id, command } = await request.json();
    const active = await env.DB.prepare('SELECT * FROM runs WHERE session_id=?').bind(session_id).first();
    if (!active || active.status !== 'started' || active.action !== 'build') return text('no active build', 409);
    if (!tool_use_id || !command || command.length > 4000) return text('bad approval', 400);
    let pending = await env.DB.prepare('SELECT * FROM approvals WHERE run_id=? AND tool_use_id=?')
      .bind(active.id, tool_use_id).first();
    if (!pending) {
      const id = crypto.randomUUID();
      await env.DB.prepare('INSERT INTO approvals(id,run_id,tool_use_id,command) VALUES(?,?,?,?)')
        .bind(id, active.id, tool_use_id, command).run();
      pending = await env.DB.prepare('SELECT * FROM approvals WHERE id=?').bind(id).first();
      await reply(env, active.chat_id, `🔐 #${active.job_id} build wants to run:\n${command.slice(0, 2000)}\n\n/yes ${active.job_id} · /no ${active.job_id}`);
    }
    return json({ id: pending.id, decision: pending.decision });
  }
  if (parts[1] === 'approval' && parts[2] && request.method === 'GET') {
    const pending = await env.DB.prepare('SELECT a.decision,r.status FROM approvals a JOIN runs r ON a.run_id=r.id WHERE a.id=?').bind(parts[2]).first();
    return pending ? json(pending) : text('not found', 404);
  }
  if (parts[1] === 'reminder' && request.method === 'POST') {
    const { chat_id, text: body, due_at } = await request.json();
    if (String(chat_id) !== String(env.TELEGRAM_OWNER_ID) || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(due_at))
      return text('bad reminder', 400);
    const result = await env.DB.prepare('INSERT INTO reminders(chat_id,text,due_at) VALUES(?,?,?)')
      .bind(chat_id, String(body).slice(0, 1000), due_at).run();
    return json({ id: result.meta.last_row_id });
  }
  return text('not found', 404);
}

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === '/health' && request.method === 'GET') return text('ok');
    if (path === '/telegram' && request.method === 'POST') return handleTelegram(request, env, ctx);
    if (path.startsWith('/backend/')) return handleBackend(request, env, path);
    return text('not found', 404);
  },
  async scheduled(_event, env) {
    const queued = await env.DB.prepare("SELECT id FROM runs WHERE status='queued' ORDER BY created_at LIMIT 20").all();
    for (const item of queued.results) await dispatchRun(env, item.id);
    const rows = await env.DB.prepare("SELECT id,chat_id,text FROM reminders WHERE status='open' AND due_at<=? ORDER BY due_at LIMIT 20")
      .bind(now()).all();
    for (const item of rows.results) {
      await env.DB.prepare("UPDATE reminders SET status='sent' WHERE id=? AND status='open'").bind(item.id).run();
      await reply(env, item.chat_id, `⏰ R${item.id} ${item.text}`);
    }
  },
};

