"""Application schema v7: workspace conversation scope and durable agent harness tables.

Applied only by ApplicationStore inside its migration transaction.
"""

# v7 records: identical to v6 plus a workspace scope for conversation/message
# (no project, no notebook). Registered Home threads live in workspace_conversations.
RECORDS_V7 = """CREATE TABLE records (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK(kind IN ('project','conversation','message','roadmap','comment','activity')),
        project_id TEXT REFERENCES records(id), notebook_id TEXT REFERENCES catalog(id),
        parent_id TEXT REFERENCES records(id), revision INTEGER NOT NULL CHECK(revision>0),
        created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)),
        CHECK((kind='project' AND project_id IS NULL AND notebook_id IS NULL) OR
              (kind IN ('conversation','message') AND project_id IS NULL) OR
              (kind!='project' AND project_id IS NOT NULL)))"""

RECORD_DEPENDENTS = [
    'CREATE INDEX history ON records(kind,project_id,sequence)',
    'CREATE INDEX replies ON records(parent_id,sequence)',
    """CREATE TRIGGER immutable_records BEFORE UPDATE ON records WHEN OLD.kind IN ('message','activity')
        BEGIN SELECT RAISE(ABORT,'append-only record'); END""",
    """CREATE TRIGGER retain_records BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'historical record'); END""",
]

RUN_STATES = ('queued', 'running', 'waiting_for_approval', 'cancelling', 'completed', 'failed', 'cancelled', 'interrupted')
TERMINAL_RUN_STATES = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})
TOOL_STATES = ('rejected', 'awaiting_approval', 'approved', 'denied', 'expired', 'executing',
               'succeeded', 'failed', 'uncertain', 'cancelled')
APPROVAL_STATES = ('pending', 'approved', 'denied', 'expired', 'invalidated')


def _in(values):
    return '(' + ','.join(f"'{v}'" for v in values) + ')'


AGENT_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS workspace_conversations (
        conversation_id TEXT PRIMARY KEY REFERENCES records(id), created_at REAL NOT NULL)""",
    f"""CREATE TABLE IF NOT EXISTS agent_runs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK(role IN ('home_coordinator','notebook_coordinator','coding_agent')),
        state TEXT NOT NULL CHECK(state IN {_in(RUN_STATES)}),
        conversation_id TEXT NOT NULL REFERENCES records(id),
        trigger_message_id TEXT REFERENCES records(id),
        notebook_id TEXT REFERENCES catalog(id), project_id TEXT REFERENCES records(id),
        parent_run_id TEXT REFERENCES agent_runs(id), depth INTEGER NOT NULL DEFAULT 0,
        task TEXT CHECK(task IS NULL OR json_valid(task)),
        operation_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL,
        budget TEXT NOT NULL CHECK(json_valid(budget)), usage TEXT NOT NULL CHECK(json_valid(usage)),
        owner TEXT, fence INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
        error TEXT, result TEXT CHECK(result IS NULL OR json_valid(result)),
        created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL)""",
    'CREATE INDEX IF NOT EXISTS agent_runs_conversation ON agent_runs(conversation_id, seq)',
    'CREATE INDEX IF NOT EXISTS agent_runs_state ON agent_runs(state, seq)',
    """CREATE TABLE IF NOT EXISTS agent_steps (
        run_id TEXT NOT NULL REFERENCES agent_runs(id), idx INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('user','assistant','tool')),
        data TEXT NOT NULL CHECK(json_valid(data)), created_at REAL NOT NULL,
        PRIMARY KEY(run_id, idx))""",
    f"""CREATE TABLE IF NOT EXISTS agent_tool_calls (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id), step_idx INTEGER NOT NULL,
        provider_call_id TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
        arguments TEXT NOT NULL CHECK(json_valid(arguments)),
        binding TEXT CHECK(binding IS NULL OR json_valid(binding)), digest TEXT,
        state TEXT NOT NULL CHECK(state IN {_in(TOOL_STATES)}),
        operation_id TEXT NOT NULL UNIQUE, result TEXT CHECK(result IS NULL OR json_valid(result)),
        created_at REAL NOT NULL, started_at REAL, finished_at REAL)""",
    'CREATE INDEX IF NOT EXISTS agent_tool_calls_run ON agent_tool_calls(run_id, step_idx)',
    f"""CREATE TABLE IF NOT EXISTS agent_approvals (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
        tool_call_id TEXT NOT NULL UNIQUE REFERENCES agent_tool_calls(id),
        digest TEXT NOT NULL, preview TEXT NOT NULL CHECK(json_valid(preview)),
        notebook_id TEXT, project_id TEXT, revision INTEGER,
        state TEXT NOT NULL CHECK(state IN {_in(APPROVAL_STATES)}),
        created_at REAL NOT NULL, expires_at REAL NOT NULL, decided_at REAL,
        decision_operation_id TEXT UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS agent_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES agent_runs(id),
        type TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)), created_at REAL NOT NULL)""",
    'CREATE INDEX IF NOT EXISTS agent_events_run ON agent_events(run_id, seq)',
    """CREATE TRIGGER IF NOT EXISTS immutable_agent_events BEFORE UPDATE ON agent_events
        BEGIN SELECT RAISE(ABORT,'append-only agent event'); END""",
    """CREATE TRIGGER IF NOT EXISTS retain_agent_events BEFORE DELETE ON agent_events
        BEGIN SELECT RAISE(ABORT,'historical agent event'); END""",
    """CREATE TRIGGER IF NOT EXISTS retain_agent_runs BEFORE DELETE ON agent_runs
        BEGIN SELECT RAISE(ABORT,'historical agent run'); END""",
]
