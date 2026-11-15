"""Durable agent runs, steps, tool calls, approvals and public events.

All writes for a claimed run are fenced by (owner, fence): a worker that lost
ownership cannot advance the run. Public events are append-only (DB trigger).
"""
import hashlib
import json
import time
import uuid

from application.store import ApplicationError, encoded
from agents.schema import TERMINAL_RUN_STATES


class LostOwnership(Exception):
    pass


def _json(value):
    return None if value is None else json.loads(value)


def fingerprint(payload):
    return hashlib.sha256(encoded(payload).encode()).hexdigest()


class AgentStore:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ helpers
    def tx(self):
        return self.app.bookkeeping()

    @staticmethod
    def _fence(db, run_id, lease):
        if lease is None: return
        owner, fence = lease
        row = db.execute('UPDATE agent_runs SET updated_at=? WHERE id=? AND owner=? AND fence=?',
                         (time.time(), run_id, owner, fence))
        if row.rowcount != 1: raise LostOwnership(run_id)

    @staticmethod
    def _event(db, run_id, kind, data):
        db.execute('INSERT INTO agent_events(run_id,type,data,created_at) VALUES (?,?,?,?)',
                   (run_id, kind, encoded(data), time.time()))

    @staticmethod
    def run_row(row):
        if row is None: return None
        result = dict(row)
        for key in ('task', 'budget', 'usage', 'result'):
            result[key] = _json(result[key])
        return result

    # ------------------------------------------------------------------ runs
    def create_run(self, *, operation_id, payload, role, conversation_id, trigger_message_id, notebook_id,
                   project_id, parent_run_id, depth, task, provider, model, budget, event=None):
        digest = fingerprint(payload)
        with self.tx() as db:
            existing = db.execute('SELECT * FROM agent_runs WHERE operation_id=?', (operation_id,)).fetchone()
            if existing:
                if existing['fingerprint'] != digest:
                    raise ApplicationError('Operation ID reused with different input', 409)
                return self.run_row(existing), False
            if db.execute('SELECT 1 FROM operations WHERE id=?', (operation_id,)).fetchone():
                raise ApplicationError('Operation ID reused with different input', 409)
            identity, now = uuid.uuid4().hex, time.time()
            db.execute('INSERT INTO agent_runs(id,role,state,conversation_id,trigger_message_id,notebook_id,project_id,'
                       'parent_run_id,depth,task,operation_id,fingerprint,provider,model,budget,usage,created_at,updated_at) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, role, 'queued', conversation_id, trigger_message_id, notebook_id, project_id,
                        parent_run_id, depth, None if task is None else encoded(task), operation_id, digest,
                        provider, model, encoded(budget), encoded({'model_calls': 0, 'tool_calls': 0, 'steps': 0,
                        'active_seconds': 0.0, 'repairs': 0, 'prompt_tokens': 0, 'completion_tokens': 0}), now, now))
            self._event(db, identity, 'run_queued', {'role': role, **(event or {})})
            return self.run_row(db.execute('SELECT * FROM agent_runs WHERE id=?', (identity,)).fetchone()), True

    def get_run(self, run_id):
        with self.tx() as db:
            row = db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None: raise ApplicationError('Agent run not found', 404)
        return self.run_row(row)

    def runs_for_conversation(self, conversation_id, *, limit=200):
        with self.tx() as db:
            rows = db.execute('SELECT * FROM agent_runs WHERE conversation_id=? ORDER BY seq DESC LIMIT ?',
                              (conversation_id, limit)).fetchall()
        return [self.run_row(r) for r in reversed(rows)]

    def queued(self, limit):
        with self.tx() as db:
            return [r['id'] for r in db.execute("SELECT id FROM agent_runs WHERE state='queued' ORDER BY seq LIMIT ?", (limit,))]

    def active_count(self):
        with self.tx() as db:
            return db.execute("SELECT count(*) FROM agent_runs WHERE state IN ('running','cancelling')").fetchone()[0]

    def claim(self, run_id, owner):
        with self.tx() as db:
            now = time.time()
            changed = db.execute("UPDATE agent_runs SET state='running',owner=?,fence=fence+1,updated_at=?,"
                                 "started_at=COALESCE(started_at,?) WHERE id=? AND state='queued' AND cancel_requested=0",
                                 (owner, now, now, run_id))
            if changed.rowcount != 1: return None
            row = self.run_row(db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone())
            self._event(db, run_id, 'run_started', {'attempt': row['fence']})
            return row

    def update(self, run_id, lease, *, event=None, **fields):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            if fields:
                values = [encoded(v) if k in ('usage', 'result') else v for k, v in fields.items()]
                db.execute('UPDATE agent_runs SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*values, run_id))
            if event: self._event(db, run_id, event[0], event[1])

    def finish(self, run_id, lease, state, *, event, error=None, result=None, usage=None):
        assert state in TERMINAL_RUN_STATES or state == 'waiting_for_approval'
        fields = {'state': state, 'error': error, 'owner': None}
        if result is not None: fields['result'] = result
        if usage is not None: fields['usage'] = usage
        if state in TERMINAL_RUN_STATES: fields['finished_at'] = time.time()
        self.update(run_id, lease, event=event, **fields)

    def event(self, run_id, kind, data, lease=None):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            self._event(db, run_id, kind, data)

    def events(self, run_id, after=0, limit=100):
        with self.tx() as db:
            if db.execute('SELECT 1 FROM agent_runs WHERE id=?', (run_id,)).fetchone() is None:
                raise ApplicationError('Agent run not found', 404)
            rows = db.execute('SELECT * FROM agent_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',
                              (run_id, after, limit + 1)).fetchall()
        items = [{'seq': r['seq'], 'type': r['type'], 'data': json.loads(r['data']), 'created_at': r['created_at']}
                 for r in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['seq'] if len(rows) > limit else None}

    def request_cancel(self, run_id):
        """Return the run state after the request. Never touches Python execution."""
        with self.tx() as db:
            row = db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            if row is None: raise ApplicationError('Agent run not found', 404)
            if row['state'] in TERMINAL_RUN_STATES: return self.run_row(row)
            now = time.time()
            if row['state'] in ('queued', 'waiting_for_approval'):
                db.execute("UPDATE agent_runs SET state='cancelled',cancel_requested=1,finished_at=?,updated_at=?,owner=NULL "
                           'WHERE id=?', (now, now, run_id))
                db.execute("UPDATE agent_approvals SET state='invalidated',decided_at=? WHERE run_id=? AND state='pending'", (now, run_id))
                db.execute("UPDATE agent_tool_calls SET state='cancelled',finished_at=? WHERE run_id=? "
                           "AND state IN ('awaiting_approval','approved')", (now, run_id))
                self._event(db, run_id, 'run_cancelled', {'note': 'Cancelled before further agent work. Already committed edits or executions are not undone.'})
            else:
                db.execute("UPDATE agent_runs SET state='cancelling',cancel_requested=1,updated_at=? WHERE id=?", (now, run_id))
                self._event(db, run_id, 'cancel_requested', {'note': 'Stopping after the current step settles. Running Python is not interrupted.'})
            return self.run_row(db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone())

    def cancel_requested(self, run_id):
        with self.tx() as db:
            return bool(db.execute('SELECT cancel_requested FROM agent_runs WHERE id=?', (run_id,)).fetchone()[0])

    # ------------------------------------------------------------------ steps
    def steps(self, run_id):
        with self.tx() as db:
            return [{'idx': r['idx'], 'kind': r['kind'], **json.loads(r['data'])}
                    for r in db.execute('SELECT * FROM agent_steps WHERE run_id=? ORDER BY idx', (run_id,))]

    def add_step(self, run_id, lease, kind, data, *, usage=None, event=None):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            idx = db.execute('SELECT COALESCE(MAX(idx),-1)+1 FROM agent_steps WHERE run_id=?', (run_id,)).fetchone()[0]
            db.execute('INSERT INTO agent_steps VALUES (?,?,?,?,?)', (run_id, idx, kind, encoded(data), time.time()))
            if usage is not None: db.execute('UPDATE agent_runs SET usage=? WHERE id=?', (encoded(usage), run_id))
            if event: self._event(db, run_id, event[0], event[1])
            return idx

    # ------------------------------------------------------------------ tool calls
    @staticmethod
    def call_row(row):
        if row is None: return None
        result = dict(row)
        for key in ('arguments', 'binding', 'result'):
            result[key] = _json(result[key])
        return result

    def tool_call(self, run_id, step_idx, provider_call_id):
        with self.tx() as db:
            return self.call_row(db.execute('SELECT * FROM agent_tool_calls WHERE run_id=? AND step_idx=? AND provider_call_id=?',
                                            (run_id, step_idx, provider_call_id)).fetchone())

    def tool_calls(self, run_id=None, state=None):
        clauses, args = [], []
        if run_id: clauses.append('run_id=?'); args.append(run_id)
        if state: clauses.append('state=?'); args.append(state)
        with self.tx() as db:
            return [self.call_row(r) for r in db.execute('SELECT * FROM agent_tool_calls' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                                                         + ' ORDER BY created_at', args)]

    def add_tool_call(self, run_id, lease, *, step_idx, provider_call_id, name, version, arguments, state,
                      binding=None, digest_value=None, result=None, approval=None, event=None, identity=None):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            identity, now = identity or uuid.uuid4().hex, time.time()
            db.execute('INSERT INTO agent_tool_calls(id,run_id,step_idx,provider_call_id,name,version,arguments,binding,digest,'
                       'state,operation_id,result,created_at,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, run_id, step_idx, provider_call_id, name, version, encoded(arguments),
                        None if binding is None else encoded(binding), digest_value, state, 'agent-' + identity,
                        None if result is None else encoded(result), now,
                        now if state == 'executing' else None, now if state in ('rejected', 'succeeded', 'failed') else None))
            if approval:
                db.execute('INSERT INTO agent_approvals(id,run_id,tool_call_id,digest,preview,notebook_id,project_id,revision,'
                           'state,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, run_id, identity, digest_value, encoded(approval['preview']),
                            approval.get('notebook_id'), approval.get('project_id'), approval.get('revision'),
                            'pending', now, now + approval['ttl']))
            if event: self._event(db, run_id, event[0], {**event[1], 'tool_call_id': identity})
            return self.call_row(db.execute('SELECT * FROM agent_tool_calls WHERE id=?', (identity,)).fetchone())

    def set_call(self, call_id, run_id, lease, *, state, result=None, event=None, started=False):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            now = time.time()
            db.execute('UPDATE agent_tool_calls SET state=?, result=COALESCE(?,result), started_at=CASE WHEN ? THEN ? ELSE started_at END, '
                       'finished_at=CASE WHEN ? IN (\'succeeded\',\'failed\',\'uncertain\',\'denied\',\'expired\',\'cancelled\') THEN ? ELSE finished_at END '
                       'WHERE id=?', (state, None if result is None else encoded(result), started, now, state, now, call_id))
            if event: self._event(db, run_id, event[0], {**event[1], 'tool_call_id': call_id})

    def merge_call_result(self, call_id, run_id, lease, partial):
        with self.tx() as db:
            self._fence(db, run_id, lease)
            row = db.execute('SELECT result FROM agent_tool_calls WHERE id=?', (call_id,)).fetchone()
            merged = {**(_json(row['result']) or {}), **partial}
            db.execute('UPDATE agent_tool_calls SET result=? WHERE id=?', (encoded(merged), call_id))

    # ------------------------------------------------------------------ approvals
    @staticmethod
    def approval_row(row):
        if row is None: return None
        result = dict(row); result['preview'] = json.loads(result['preview'])
        return result

    def approvals(self, run_id):
        with self.tx() as db:
            return [self.approval_row(r) for r in db.execute(
                'SELECT agent_approvals.*, agent_tool_calls.name AS tool FROM agent_approvals JOIN agent_tool_calls '
                'ON agent_tool_calls.id=tool_call_id WHERE agent_approvals.run_id=? ORDER BY agent_approvals.created_at', (run_id,))]

    def approval_for_call(self, call_id):
        with self.tx() as db:
            return self.approval_row(db.execute('SELECT * FROM agent_approvals WHERE tool_call_id=?', (call_id,)).fetchone())

    def decide(self, approval_id, *, operation_id, decision, digest_value):
        """Single-use, digest-bound owner decision. Exact replay returns the same outcome."""
        outcome = self._decide(approval_id, operation_id, decision, digest_value)
        if outcome == 'expired': raise ApplicationError('Approval expired', 409)
        return outcome

    def _decide(self, approval_id, operation_id, decision, digest_value):
        with self.tx() as db:
            row = db.execute('SELECT * FROM agent_approvals WHERE id=?', (approval_id,)).fetchone()
            if row is None: raise ApplicationError('Approval not found', 404)
            expected = 'approved' if decision == 'approve' else 'denied'
            if row['decision_operation_id'] == operation_id:
                if row['state'] != expected: raise ApplicationError('Operation ID reused with different input', 409)
                return self.approval_row(row), False
            other = db.execute('SELECT 1 FROM agent_approvals WHERE decision_operation_id=?', (operation_id,)).fetchone()
            if other: raise ApplicationError('Operation ID reused with different input', 409)
            if row['digest'] != digest_value:
                raise ApplicationError('Approval digest does not match the proposed action', 409)
            now = time.time()
            if row['state'] == 'pending' and row['expires_at'] < now:
                db.execute("UPDATE agent_approvals SET state='expired',decided_at=? WHERE id=?", (now, approval_id))
                db.execute("UPDATE agent_tool_calls SET state='expired',finished_at=? WHERE id=?", (now, row['tool_call_id']))
                db.execute("UPDATE agent_runs SET state='queued',updated_at=? WHERE id=? AND state='waiting_for_approval'",
                           (now, row['run_id']))
                self._event(db, row['run_id'], 'approval_expired', {'approval_id': approval_id})
                return 'expired'
            if row['state'] != 'pending':
                raise ApplicationError(f"Approval is already {row['state']}", 409)
            run = db.execute('SELECT state FROM agent_runs WHERE id=?', (row['run_id'],)).fetchone()
            if run['state'] != 'waiting_for_approval':
                raise ApplicationError('Run is not waiting for this approval', 409)
            db.execute('UPDATE agent_approvals SET state=?,decided_at=?,decision_operation_id=? WHERE id=?',
                       (expected, now, operation_id, approval_id))
            db.execute('UPDATE agent_tool_calls SET state=? WHERE id=?', ('approved' if expected == 'approved' else 'denied', row['tool_call_id']))
            db.execute("UPDATE agent_runs SET state='queued',updated_at=? WHERE id=?", (now, row['run_id']))
            self._event(db, row['run_id'], 'approval_decided', {'approval_id': approval_id, 'decision': expected})
            return self.approval_row(db.execute('SELECT * FROM agent_approvals WHERE id=?', (approval_id,)).fetchone()), True

    def expire_approvals(self):
        """Expire overdue pending approvals and re-queue their runs so the model learns of it."""
        with self.tx() as db:
            now = time.time()
            rows = db.execute("SELECT * FROM agent_approvals WHERE state='pending' AND expires_at<?", (now,)).fetchall()
            for row in rows:
                db.execute("UPDATE agent_approvals SET state='expired',decided_at=? WHERE id=?", (now, row['id']))
                db.execute("UPDATE agent_tool_calls SET state='expired',finished_at=? WHERE id=?", (now, row['tool_call_id']))
                db.execute("UPDATE agent_runs SET state='queued',updated_at=? WHERE id=? AND state='waiting_for_approval'",
                           (now, row['run_id']))
                self._event(db, row['run_id'], 'approval_expired', {'approval_id': row['id']})
            return len(rows)

    def active_child(self, notebook_id):
        with self.tx() as db:
            row = db.execute("SELECT id FROM agent_runs WHERE role='coding_agent' AND notebook_id=? AND state NOT IN "
                             "('completed','failed','cancelled','interrupted')", (notebook_id,)).fetchone()
            return row['id'] if row else None

    # ------------------------------------------------------------------ recovery
    def unowned_active_runs(self):
        with self.tx() as db:
            return [self.run_row(r) for r in db.execute("SELECT * FROM agent_runs WHERE state IN ('running','cancelling') ORDER BY seq")]

    def recover_claim(self, run_id, owner):
        """Take a run left running by a previous control plane (single-owner storage lock held)."""
        with self.tx() as db:
            db.execute('UPDATE agent_runs SET owner=?,fence=fence+1,updated_at=? WHERE id=?', (owner, time.time(), run_id))
            return self.run_row(db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone())
