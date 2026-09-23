import assert from 'node:assert/strict';
import test from 'node:test';
import worker from './worker.mjs';

test('health endpoint is available without secrets', async () => {
  const response = await worker.fetch(new Request('https://example.workers.dev/health'), {}, {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'ok');
});

test('Telegram rejects an invalid webhook secret before reading the body', async () => {
  const response = await worker.fetch(new Request('https://example.workers.dev/telegram', {
    method: 'POST', body: 'not JSON',
    headers: { 'x-telegram-bot-api-secret-token': 'wrong' },
  }), { TELEGRAM_WEBHOOK_SECRET: 'right' }, {});
  assert.equal(response.status, 403);
});

test('backend rejects requests without its bearer token', async () => {
  const response = await worker.fetch(new Request('https://example.workers.dev/backend/run/abc'),
    { BACKEND_TOKEN: 'secret' }, {});
  assert.equal(response.status, 403);
});

test('a cloud session that never registers fails and alerts the owner', async () => {
  const queries = [];
  const messages = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, options) => {
    messages.push(JSON.parse(options.body));
    return new Response('{}');
  };
  const DB = {
    prepare(sql) {
      queries.push(sql);
      return {
        bind() { return this; },
        async all() {
          if (sql.includes("status='starting'")) return { results: [{
            id: 'run-1', job_id: 1, chat_id: 123, session_url: 'https://claude.ai/code/test',
          }] };
          return { results: [] };
        },
        async first() { return { id: 'run-1' }; },
        async run() { return { meta: { changes: 1 } }; },
      };
    },
  };
  try {
    await worker.scheduled({}, { DB, TELEGRAM_BOT_TOKEN: 'test' });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.ok(queries.some(sql => sql.includes("UPDATE runs SET status='failed'")));
  assert.ok(queries.some(sql => sql.includes("UPDATE jobs SET status='failed'")));
  assert.equal(messages.length, 1);
  assert.match(messages[0].text, /did not connect within 10 minutes/);
  assert.equal(messages[0].chat_id, 123);
});

