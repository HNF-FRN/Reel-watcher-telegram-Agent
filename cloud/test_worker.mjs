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

