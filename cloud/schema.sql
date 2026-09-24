-- Fresh install. An existing database from the first cloud version: run upgrade-2.sql instead.
CREATE TABLE IF NOT EXISTS updates (
  update_id INTEGER PRIMARY KEY,
  received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One library shared with the PC: origin 'pc' rows are copies of the PC's reels (pushed by pc_link.py),
-- origin 'cloud' rows were made here. pulled=0 marks a cloud-side change the PC has not copied yet.
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  summary TEXT NOT NULL DEFAULT '',
  breakdown TEXT NOT NULL DEFAULT '',
  plan TEXT NOT NULL DEFAULT '',
  branch_url TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'cloud',
  media_group TEXT,
  pulled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS jobs_pulled ON jobs(pulled);
CREATE INDEX IF NOT EXISTS jobs_group ON jobs(media_group) WHERE media_group IS NOT NULL;

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  job_id INTEGER,
  action TEXT NOT NULL,
  chat_id INTEGER NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  session_id TEXT,
  session_url TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS runs_session ON runs(session_id) WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tool_use_id TEXT NOT NULL,
  command TEXT NOT NULL,
  decision TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TEXT,
  UNIQUE(run_id, tool_use_id)
);
CREATE INDEX IF NOT EXISTS approvals_pending ON approvals(run_id, decision, created_at);

-- Same ids and fields as reminders/remind.py. due_local is the owner's wall-clock time; sent_due records which
-- occurrence was delivered, so the PC and the cloud never both send it. changed=1: the PC hasn't copied it yet.
CREATE TABLE IF NOT EXISTS reminders (
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
