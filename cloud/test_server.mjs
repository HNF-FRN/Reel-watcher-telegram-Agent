// A local copy of the Worker (workerd + an empty D1 database, fake Telegram) for test_pc_link.py.
// Prints "READY <url>" and runs until stdin closes.
import { readFileSync } from 'node:fs';
import { Miniflare } from 'miniflare';

const mf = new Miniflare({
  modules: true, scriptPath: new URL('./worker.mjs', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'),
  compatibilityDate: '2026-08-01', d1Databases: ['DB'], port: 0,
  outboundService: () => Response.json({ ok: true, result: { url: '', pending_update_count: 0, message_id: 1 } }),
  bindings: {
    TELEGRAM_BOT_TOKEN: '123:TEST', TELEGRAM_OWNER_ID: '42', TELEGRAM_WEBHOOK_SECRET: 'hook-secret',
    BACKEND_TOKEN: 'backend-secret', TELEGRAM_API: 'https://tg.test', PUBLIC_URL: 'https://reel.example.workers.dev',
  },
});
const url = await mf.ready;
const db = await mf.getD1Database('DB');
const schema = readFileSync(new URL('./schema.sql', import.meta.url), 'utf8');
await db.batch(schema.replace(/--.*$/gm, '').split(';').map(s => s.trim()).filter(Boolean).map(s => db.prepare(s)));
console.log(`READY ${url.origin}`);
process.stdin.resume();
process.stdin.on('end', async () => { await mf.dispose(); process.exit(0); });
