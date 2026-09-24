-- One-time upgrade of a database created by the first cloud version (failover, shared library, reminders).
-- npx wrangler d1 execute reel-agent-cloud --remote --file=upgrade-2.sql
ALTER TABLE jobs ADD COLUMN tags TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'cloud';
ALTER TABLE jobs ADD COLUMN media_group TEXT;
ALTER TABLE jobs ADD COLUMN pulled INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS jobs_pulled ON jobs(pulled);
CREATE INDEX IF NOT EXISTS jobs_group ON jobs(media_group) WHERE media_group IS NOT NULL;

-- The first version's reminders table (filled by routine runs) is kept as reminders_v1; the new one mirrors remind.py.
ALTER TABLE reminders RENAME TO reminders_v1;
CREATE TABLE reminders (
  rid TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  note TEXT,
  source TEXT,
  every TEXT,
  due_local TEXT,
  due_utc TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  sent_due TEXT,
  origin TEXT NOT NULL DEFAULT 'pc',
  changed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status, due_utc);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
