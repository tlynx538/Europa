"""Version 7 extension of the existing application database, not a chat database."""
TABLES = ('agent_conversations', 'agent_runs', 'agent_events', 'agent_messages', 'agent_turns', 'agent_calls', 'agent_approvals', 'agent_handoffs')

SCHEMA = [
    '''CREATE TABLE agent_conversations (
        scope_key TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('workspace','project')),
        resource_id TEXT, conversation_id TEXT NOT NULL UNIQUE REFERENCES records(id))''',
    '''CREATE TABLE agent_runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES records(id),
        parent_id TEXT REFERENCES agent_runs(id), role TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)), grant_json TEXT NOT NULL CHECK(json_valid(grant_json)),
        budgets TEXT NOT NULL CHECK(json_valid(budgets)), provider TEXT NOT NULL, model TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','waiting_for_approval','cancelling',
            'completed','failed','cancelled','interrupted','needs_reconciliation')),
        created_at REAL NOT NULL, updated_at REAL NOT NULL, deadline REAL NOT NULL,
        owner TEXT, generation INTEGER NOT NULL DEFAULT 0, lease_until REAL,
        steps INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
        tools INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, repairs INTEGER NOT NULL DEFAULT 0,
        depth INTEGER NOT NULL DEFAULT 0, error TEXT)''',
    'CREATE INDEX agent_queue ON agent_runs(state,created_at,id)',
    'CREATE INDEX agent_conversation_runs ON agent_runs(conversation_id)',
    '''CREATE TABLE agent_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES agent_runs(id),
        created_at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)))''',
    'CREATE INDEX agent_event_resume ON agent_events(run_id,sequence)',
    '''CREATE TRIGGER immutable_agent_events BEFORE UPDATE ON agent_events
        BEGIN SELECT RAISE(ABORT,'append-only agent event'); END''',
    '''CREATE TRIGGER retain_agent_events BEFORE DELETE ON agent_events
        BEGIN SELECT RAISE(ABORT,'historical agent event'); END''',
    '''CREATE TABLE agent_messages (
        message_id TEXT PRIMARY KEY REFERENCES records(id), run_id TEXT NOT NULL REFERENCES agent_runs(id),
        purpose TEXT NOT NULL CHECK(purpose IN ('user','assistant')))''',
    '''CREATE TABLE agent_turns (
        run_id TEXT NOT NULL REFERENCES agent_runs(id), number INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('started','completed','interrupted')),
        context_digest TEXT NOT NULL, response TEXT CHECK(response IS NULL OR json_valid(response)),
        PRIMARY KEY(run_id,number))''',
    '''CREATE TABLE agent_calls (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id), call_id TEXT NOT NULL,
        turn_number INTEGER NOT NULL, tool TEXT NOT NULL, version INTEGER NOT NULL,
        classification TEXT NOT NULL CHECK(classification IN ('read','write','execute','delegate')),
        arguments TEXT NOT NULL CHECK(json_valid(arguments)), digest TEXT NOT NULL,
        target TEXT NOT NULL CHECK(json_valid(target)), operation_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('proposed','waiting','approved','started','completed',
            'failed','uncertain','cancelled')),
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        result TEXT CHECK(result IS NULL OR json_valid(result)), error TEXT,
        UNIQUE(run_id,call_id))''',
    '''CREATE TABLE agent_approvals (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
        call_id TEXT NOT NULL UNIQUE REFERENCES agent_calls(id), digest TEXT NOT NULL,
        expires_at REAL NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('pending','granted','denied','expired','cancelled')),
        decided_at REAL, actor TEXT)''',
    "CREATE INDEX agent_approval_expiry ON agent_approvals(expires_at) WHERE decision='pending'",
]

HANDOFF_SCHEMA = [
    """CREATE TABLE agent_handoffs (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
        operation_id TEXT NOT NULL UNIQUE REFERENCES agent_calls(operation_id),
        source_conversation_id TEXT NOT NULL REFERENCES records(id),
        destination_conversation_id TEXT NOT NULL REFERENCES records(id),
        project_id TEXT NOT NULL REFERENCES records(id), notebook_id TEXT NOT NULL REFERENCES catalog(id),
        path TEXT NOT NULL, plan TEXT NOT NULL CHECK(json_valid(plan)), created_at REAL NOT NULL)""",
    'CREATE INDEX agent_handoff_source ON agent_handoffs(source_conversation_id,created_at)',
    'CREATE INDEX agent_handoff_destination ON agent_handoffs(destination_conversation_id,created_at)',
]
