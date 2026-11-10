"""Run, approval and effect journals inside ApplicationStore's private database."""
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import time
import uuid

from application.models import MessageCreate
from application.store import ApplicationError, encoded
from agents.models import Scope
from notebooks.store import NotebookError
from workspace.browser import WorkspaceError

ACTIVE = ('queued', 'running', 'waiting_for_approval', 'cancelling')
TRANSITIONS = {
    'queued': {'running', 'failed', 'cancelled'},
    'running': {'waiting_for_approval', 'completed', 'failed', 'cancelling', 'interrupted', 'needs_reconciliation'},
    'waiting_for_approval': {'queued', 'cancelled', 'failed'},
    'cancelling': {'cancelled', 'needs_reconciliation'},
    'interrupted': {'queued', 'cancelled'},
    'needs_reconciliation': set(), 'completed': set(), 'failed': set(), 'cancelled': set(),
}
LEASE_SECONDS = 30
MAX_ACTIVE = 8


def identity(): return uuid.uuid4().hex


def digest(value): return hashlib.sha256(encoded(value).encode()).hexdigest()


class AgentFault(ApplicationError):
    pass


class FenceLost(AgentFault):
    def __init__(self): super().__init__('Agent run ownership changed', 409)


class AgentStore:
    def __init__(self, application, workspace, redactor):
        self.app, self.workspace, self.redactor = application, workspace, redactor
        info = os.fstat(workspace._root_fd)
        self.root = [info.st_dev, info.st_ino]

    @property
    def db(self): return self.app.db

    @contextmanager
    def transaction(self):
        # Agent bookkeeping does not rescan/rewrite the notebook catalog on each event.
        # Shared application ownership and lock order still protect all resources.
        with self.app.notebooks.locked(), self.app.mutex:
            if self.db is None: raise AgentFault('Agent storage is closed', 503)
            try:
                self.db.execute('BEGIN IMMEDIATE')
                yield
                self.db.commit()
            except BaseException as exc:
                self.db.rollback()
                if isinstance(exc, sqlite3.Error): raise AgentFault('Agent journal transaction failed', 503) from exc
                raise

    def _run(self, run):
        row = self.db.execute('SELECT * FROM agent_runs WHERE id=?', (run,)).fetchone()
        if not row: raise AgentFault('Agent run not found', 404)
        result = dict(row)
        for key in ('scope', 'grant_json', 'budgets'): result[key] = json.loads(result[key])
        return result

    def _fence(self, run, owner, generation, *, cancelling=False):
        row = self._run(run)
        if (row['owner'] != owner or row['generation'] != generation or (row['lease_until'] or 0) <= time.time()
                or row['state'] not in ({'running', 'cancelling'} if cancelling else {'running'})):
            raise FenceLost()
        return row

    def _event(self, run, kind, **data):
        value = encoded(self.redactor.value(data))
        if len(value.encode()) > 24576: raise AgentFault('Public agent event exceeds limit', 413)
        self.db.execute('INSERT INTO agent_events(run_id,created_at,kind,data) VALUES (?,?,?,?)',
                        (run, time.time(), kind, value))

    def _transition(self, row, target, *, error=None):
        if target not in TRANSITIONS[row['state']]: raise AgentFault('Illegal agent state transition', 409)
        error = self.redactor.text(error) if error else None
        active = target in ('running', 'cancelling')
        self.db.execute('UPDATE agent_runs SET state=?,updated_at=?,error=?,owner=?,lease_until=? WHERE id=?',
                        (target, time.time(), error, row['owner'] if active else None,
                         row['lease_until'] if active else None, row['id']))
        self._event(row['id'], 'run_' + target, state=target, **({'reason': error} if error else {}))

    def _replayed(self, operation, fingerprint):
        previous = self.db.execute('SELECT * FROM operations WHERE id=?', (operation,)).fetchone()
        if not previous: return None
        if previous['fingerprint'] != fingerprint: raise AgentFault('Operation ID reused with different input', 409)
        return json.loads(previous['response'])

    def _operation(self, operation, action, body, work):
        fingerprint = digest([action, body])
        previous = self._replayed(operation, fingerprint)
        if previous is not None: return previous
        result = work()
        self.db.execute('INSERT INTO operations VALUES (?,?,?)', (operation, fingerprint, encoded(result)))
        return result

    def _conversation(self, scope):
        if scope.kind == 'notebook':
            self.app._notebook_scope(scope.resource_id)
            row = self.db.execute('SELECT conversation_id FROM canonical_notebook_conversations WHERE notebook_id=?', (scope.resource_id,)).fetchone()
            if not row: raise AgentFault('Open the canonical notebook conversation first', 409)
            return self.app._view(self.app._row(row['conversation_id'], 'conversation'))
        if scope.kind == 'project': self.app._project(scope.resource_id)
        key = ':'.join(map(str, ['workspace', *self.root])) if scope.kind == 'workspace' else 'project:' + scope.resource_id
        existing = self.db.execute('SELECT conversation_id FROM agent_conversations WHERE scope_key=?', (key,)).fetchone()
        if existing: return self.app._view(self.app._row(existing['conversation_id'], 'conversation'))
        record = self.app._insert('conversation', scope.resource_id, None, {'title': 'Workspace conversation' if scope.kind == 'workspace' else 'Project conversation'})
        self.db.execute('INSERT INTO agent_conversations VALUES (?,?,?,?)', (key, scope.kind, scope.resource_id, record['id']))
        return record

    def conversation(self, scope):
        if scope.kind == 'notebook': return self.app.ensure_notebook_conversation(scope.resource_id)
        with self.transaction(): return self._conversation(scope)

    def _grant(self, scope, notebooks):
        if scope.kind != 'workspace' and (notebooks or scope.directory):
            raise AgentFault('Notebook and project scopes derive their own grants', 403)
        project, directory = None, None
        if scope.kind == 'workspace':
            self.workspace.list(scope.directory, None, 1)
            directory = scope.directory
        elif scope.kind == 'project':
            project = scope.resource_id
            self.app._project(project)
            notebooks = [r[0] for r in self.db.execute('SELECT notebook_id FROM memberships WHERE project_id=? AND associated=1 ORDER BY notebook_id LIMIT 20', (project,))]
            binding = self.db.execute('SELECT path FROM project_bindings WHERE project_id=? AND root_device=? AND root_inode=?', (project, *self.root)).fetchone()
            if binding and self.workspace.project(binding['path'])['id'] == project: directory = binding['path']
        else:
            notebooks = [scope.resource_id]
            membership = self.db.execute('SELECT project_id FROM memberships WHERE notebook_id=? AND associated=1', (scope.resource_id,)).fetchone()
            if membership:
                self.app._project(membership[0]); project = membership[0]
        allowed = []
        for notebook in sorted(set(notebooks)):
            try: self.app._notebook_scope(notebook)
            except NotebookError:
                if scope.kind == 'project': continue
                raise
            allowed.append(notebook)
        return {'capabilities': ['read'], 'notebooks': allowed, 'project_id': project, 'directory': directory, 'root': self.root}

    def _message(self, row, content, purpose, **links):
        author = 'user' if purpose == 'user' else row['role']
        message = self.app._append_message(row['conversation_id'],
            MessageCreate(operation_id=identity(), content=self.redactor.text(content), **links), author)
        self.db.execute('INSERT INTO agent_messages VALUES (?,?,?)', (message['id'], row['id'], purpose))
        return message['id']

    def create(self, body, provider):
        # Preserve exact v7 replay identity for the original read-only contract.
        payload = body.model_dump(exclude={'mode'} if body.mode=='discuss' else set())
        with self.app.mutex:
            previous = self._replayed(body.operation_id, digest(['agent/create', payload]))
            if previous is not None: return previous
        if not provider['enabled']: raise AgentFault('Agent provider is not configured', 503)
        if body.scope.kind == 'notebook': self.app.ensure_notebook_conversation(body.scope.resource_id)
        with self.transaction():
            def work():
                if self.db.execute("SELECT COUNT(*) FROM agent_runs WHERE state IN ('queued','running','waiting_for_approval','cancelling')").fetchone()[0] >= MAX_ACTIVE:
                    raise AgentFault('Agent run capacity reached', 429)
                conversation = self._conversation(body.scope)
                grant = self._grant(body.scope, body.authorized_notebooks)
                if body.mode == 'coordinate' and body.scope.kind == 'workspace':
                    grant['capabilities'].append('workspace.create')
                run, now = identity(), time.time()
                self.db.execute('''INSERT INTO agent_runs(id,conversation_id,role,scope,grant_json,budgets,provider,model,state,created_at,updated_at,deadline)
                    VALUES (?,?,'coordinator',?,?,?, ?,?,'queued',?,?,?)''',
                    (run, conversation['id'], encoded(body.scope.model_dump()), encoded(grant), encoded(body.budgets.model_dump()),
                     provider['provider'], provider['model'], now, now, now + body.budgets.wall_seconds))
                message = self._message(self._run(run), body.content, 'user')
                self._event(run, 'run_queued', message_id=message, provider=provider['provider'], model=provider['model'])
                return {'id': run, 'conversation_id': conversation['id'], 'message_id': message}
            return self._operation(body.operation_id, 'agent/create', payload, work)

    def _approval_view(self, row):
        return {'id': row['id'], 'run_id': row['run_id'], 'call_id': row['call_id'], 'digest': row['digest'],
                'expires_at': row['expires_at'], 'decision': row['decision'], 'decided_at': row['decided_at'], 'actor': row['actor'],
                'tool': row['tool'], 'version': row['version'], 'target': json.loads(row['target']), 'arguments': json.loads(row['arguments'])}

    def _approval(self, run, approval=None):
        sql = '''SELECT a.*,c.tool,c.version,c.target,c.arguments FROM agent_approvals a
                 JOIN agent_calls c ON c.id=a.call_id WHERE a.run_id=?'''
        row = self.db.execute(sql + (' AND a.id=?' if approval else " AND a.decision='pending'"), (run, approval) if approval else (run,)).fetchone()
        if row is None and approval: raise AgentFault('Approval not found for this run', 404)
        return self._approval_view(row) if row else None

    def _public(self, row):
        view = {key: row[key] for key in ('id', 'conversation_id', 'parent_id', 'role', 'scope', 'state', 'provider', 'model',
                                         'created_at', 'updated_at', 'deadline', 'error', 'budgets', 'depth')}
        view['usage'] = {key: row[key] for key in ('steps', 'requests', 'tools', 'tokens', 'output_tokens', 'repairs')}
        view['approval'] = self._approval(row['id'])
        view['messages'] = [dict(r) for r in self.db.execute('SELECT message_id,purpose FROM agent_messages WHERE run_id=? ORDER BY rowid', (row['id'],))]
        return self.redactor.value(view)

    def get(self, run):
        with self.app.mutex: return self._public(self._run(run))

    def list(self, conversation, before, limit):
        with self.app.mutex:
            self.app._row(conversation, 'conversation')
            rows = self.db.execute('SELECT id,rowid FROM agent_runs WHERE conversation_id=? AND rowid<? ORDER BY rowid DESC LIMIT ?',
                                   (conversation, before or 2**63-1, limit + 1)).fetchall()
            identities = [r['id'] for r in rows[:limit]]
            if not before:
                active = self.db.execute("SELECT id FROM agent_runs WHERE conversation_id=? AND state IN ('queued','running','waiting_for_approval','cancelling') ORDER BY rowid DESC LIMIT ?", (conversation,MAX_ACTIVE)).fetchall()
                identities.extend(r['id'] for r in active if r['id'] not in identities)
            return {'items': [self._public(self._run(run)) for run in identities],
                    'next_before': rows[limit-1]['rowid'] if len(rows) > limit else None}

    def events(self, run, after, limit):
        with self.app.mutex:
            self._run(run)
            rows = self.db.execute('SELECT sequence,created_at,kind,data FROM agent_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                                   (run, after, limit + 1)).fetchall()
            items = [dict(r, data=json.loads(r['data'])) for r in rows[:limit]]
            return {'items': items, 'next_after': items[-1]['sequence'] if items else after, 'has_more': len(rows) > limit}

    def _expire(self):
        now = time.time()
        for row in self.db.execute("SELECT id FROM agent_runs WHERE state='queued' AND deadline<=?", (now,)).fetchall():
            self._transition(self._run(row['id']), 'failed', error='Run deadline expired')
        for approval in self.db.execute("SELECT * FROM agent_approvals WHERE decision='pending' AND expires_at<=?", (now,)).fetchall():
            self.db.execute("UPDATE agent_approvals SET decision='expired',decided_at=?,actor='system' WHERE id=?", (now, approval['id']))
            self.db.execute("UPDATE agent_calls SET state='cancelled',updated_at=? WHERE id=?", (now, approval['call_id']))
            self._event(approval['run_id'], 'approval_expired', approval_id=approval['id'])
            self._transition(self._run(approval['run_id']), 'failed', error='Approval expired')

    def claim(self, owner):
        with self.transaction():
            self._expire()
            row = self.db.execute("SELECT id FROM agent_runs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row: return None
            self.db.execute("UPDATE agent_runs SET owner=?,generation=generation+1,lease_until=? WHERE id=?", (owner, time.time()+LEASE_SECONDS, row['id']))
            self._transition(self._run(row['id']), 'running')
            return self._run(row['id'])

    def renew(self, run, owner, generation):
        with self.transaction():
            self._fence(run, owner, generation, cancelling=True)
            self.db.execute('UPDATE agent_runs SET lease_until=? WHERE id=?', (time.time()+LEASE_SECONDS, run))

    def inspect(self, run, owner, generation):
        with self.app.mutex: return self._fence(run, owner, generation, cancelling=True)

    def model_start(self, run, owner, generation, context, input_cost):
        with self.transaction():
            row = self._fence(run, owner, generation)
            b = row['budgets']
            output = min(2048, b['output_tokens']-row['output_tokens'], b['total_tokens']-row['tokens']-input_cost)
            if (row['requests'] >= b['requests'] or row['steps'] >= b['steps'] or input_cost > b['context_tokens']
                    or output < 1 or row['deadline'] <= time.time()): raise AgentFault('Model request budget exhausted', 409)
            number = row['requests'] + 1
            self.db.execute('UPDATE agent_runs SET requests=?,steps=steps+1,tokens=tokens+? WHERE id=?', (number, input_cost, run))
            self.db.execute("INSERT INTO agent_turns VALUES (?,?,'started',?,NULL)", (run, number, digest(context)))
            self._event(run, 'model_requested', request=number, context_cost=input_cost, output_limit=output)
            return number, output

    def model_done(self, run, owner, generation, number, reply, input_cost, output_limit):
        with self.transaction():
            row = self._fence(run, owner, generation, cancelling=True)
            turn = self.db.execute('SELECT state FROM agent_turns WHERE run_id=? AND number=?', (run, number)).fetchone()
            if not turn or turn['state'] != 'started': raise AgentFault('Model request is no longer active', 409)
            payload = self.redactor.value(reply.model_dump())
            # Conservative bytes-as-token charge plus provider-reported usage.
            content_cost = len(reply.content.encode()) + sum(len(encoded(c.model_dump()).encode()) for c in reply.calls)
            output = max(reply.output_tokens, content_cost)
            extra = max(0, reply.input_tokens-input_cost) + output
            self.db.execute('UPDATE agent_runs SET tokens=tokens+?,output_tokens=output_tokens+? WHERE id=?', (extra, output, run))
            if row['state'] == 'cancelling':
                self.db.execute("UPDATE agent_turns SET state='interrupted' WHERE run_id=? AND number=?", (run, number))
                self._transition(row, 'cancelled')
                return False
            if (output > output_limit or row['tokens']+extra > row['budgets']['total_tokens'] or
                    reply.input_tokens > row['budgets']['context_tokens'] or row['deadline'] <= time.time()):
                self.db.execute("UPDATE agent_turns SET state='interrupted' WHERE run_id=? AND number=?", (run, number))
                self._transition(row, 'failed', error='Model response exceeded run budget')
                return False
            self.db.execute("UPDATE agent_turns SET state='completed',response=? WHERE run_id=? AND number=? AND state='started'", (encoded(payload), run, number))
            self._event(run, 'model_responded', request=number, proposed_tools=len(reply.calls),
                        **({'message': payload['content']} if reply.calls and payload['content'] else {}))
            return True

    def _call(self, identity):
        result = dict(self.db.execute('SELECT * FROM agent_calls WHERE id=?', (identity,)).fetchone())
        for key in ('arguments','target','result'):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    def propose(self, run, owner, generation, number, call, tool, arguments, target):
        with self.transaction():
            row = self._fence(run, owner, generation)
            if row['tools'] >= row['budgets']['tools'] or row['steps'] >= row['budgets']['steps']:
                raise AgentFault('Tool call budget exhausted', 409)
            if self.db.execute('SELECT 1 FROM agent_calls WHERE run_id=? AND call_id=?', (run, call.id)).fetchone():
                raise AgentFault('Duplicate model tool-call identifier', 409)
            if self.redactor.value(arguments) != arguments or self.redactor.text(call.id) != call.id:
                raise AgentFault('Tool proposal contains a credential', 403)
            key, op, now = identity(), 'agent-' + identity(), time.time()
            fingerprint = digest([run, tool.name, tool.version, target, arguments])
            self.db.execute('''INSERT INTO agent_calls(id,run_id,call_id,turn_number,tool,version,classification,arguments,digest,target,operation_id,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (key, run, call.id, number, tool.name, tool.version, tool.classification,
                encoded(arguments), fingerprint, encoded(target), op, 'waiting' if tool.approval else 'proposed', now, now))
            self.db.execute('UPDATE agent_runs SET tools=tools+1,steps=steps+1 WHERE id=?', (run,))
            self._event(run, 'tool_proposed', call_id=key, tool=tool.name, version=tool.version, target=target, arguments=arguments)
            if tool.approval:
                approval, expiry = identity(), min(row['deadline'], now + 300)
                self.db.execute("INSERT INTO agent_approvals VALUES (?,?,?,?,?,'pending',NULL,NULL)", (approval, run, key, fingerprint, expiry))
                self._event(run, 'approval_requested', approval_id=approval, call_id=key, digest=fingerprint, expires_at=expiry)
                self._transition(row, 'waiting_for_approval')
            return self._call(key)

    def decide(self, run, approval, body):
        with self.transaction():
            def work():
                self._expire()
                row, review = self._run(run), self._approval(run, approval)
                if review['digest'] != body.digest: raise AgentFault('Approval digest does not match', 409)
                if review['decision'] != 'pending':
                    if review['decision'] in (body.decision, 'expired'): return review
                    raise AgentFault('Approval already decided', 409)
                if row['state'] != 'waiting_for_approval': raise AgentFault('Run is not waiting for this approval', 409)
                now = time.time()
                self.db.execute("UPDATE agent_approvals SET decision=?,decided_at=?,actor='owner' WHERE id=?", (body.decision, now, approval))
                self.db.execute('UPDATE agent_calls SET state=?,updated_at=? WHERE id=?', ('approved' if body.decision == 'granted' else 'cancelled', now, review['call_id']))
                self._event(run, 'approval_' + body.decision, approval_id=approval, call_id=review['call_id'])
                self._transition(row, 'queued' if body.decision == 'granted' else 'cancelled')
                return self._approval(run, approval)
            return self._operation(body.operation_id, 'agent/approve/' + run + '/' + approval, body.model_dump(), work)

    def approved(self, run, owner, generation):
        with self.app.mutex:
            self._fence(run, owner, generation)
            row = self.db.execute("SELECT id FROM agent_calls WHERE run_id=? AND state='approved' ORDER BY created_at LIMIT 1", (run,)).fetchone()
            return self._call(row['id']) if row else None

    def begin_call(self, run, owner, generation, call_id):
        with self.transaction():
            row = self._fence(run, owner, generation)
            call = self._call(call_id)
            if call['run_id'] != run or call['state'] not in ('proposed','approved') or row['deadline'] <= time.time():
                raise AgentFault('Tool call is no longer eligible', 409)
            if call['classification'] != 'read' or call['state'] == 'approved':
                approval = self.db.execute("SELECT id FROM agent_approvals WHERE run_id=? AND call_id=? AND digest=? AND decision='granted' AND expires_at>?", (run, call_id, call['digest'], time.time())).fetchone()
                if not approval: raise AgentFault('Exact unexpired approval is required', 403)
            self.db.execute("UPDATE agent_calls SET state='started',updated_at=? WHERE id=?", (time.time(), call_id))
            self._event(run, 'tool_started', call_id=call_id, tool=call['tool'])
            return self._call(call_id)

    def end_call(self, run, owner, generation, call_id, state, *, result=None, error=None):
        if state not in ('completed','failed','uncertain'): raise ValueError('Invalid tool outcome')
        with self.transaction():
            row = self._fence(run, owner, generation, cancelling=True)
            call = self._call(call_id)
            if call['run_id'] != run or call['state'] != 'started': raise AgentFault('Tool call is not in flight', 409)
            result = self.redactor.value(result)
            payload = encoded(result) if result is not None else None
            if payload and len(payload.encode()) > 16384: raise AgentFault('Tool result exceeds storage limit', 413)
            self.db.execute('UPDATE agent_calls SET state=?,updated_at=?,result=?,error=? WHERE id=?', (state, time.time(), payload, error, call_id))
            self._event(run, 'tool_' + state, call_id=call_id, tool=call['tool'], result=result, error=error)
            if state == 'uncertain': self._transition(row, 'needs_reconciliation', error='Tool outcome could not be verified')
            elif row['state'] == 'cancelling': self._transition(row, 'cancelled')

    def finish(self, run, owner, generation, content):
        with self.transaction():
            row = self._fence(run, owner, generation, cancelling=True)
            if row['state'] == 'cancelling': self._transition(row, 'cancelled'); return
            if row['deadline'] <= time.time(): self._transition(row, 'failed', error='Run deadline expired'); return
            message = self._message(row, content, 'assistant')
            self._event(run, 'reply_saved', message_id=message)
            self._transition(row, 'completed')

    def fail(self, run, owner, generation, reason):
        with self.transaction():
            row = self._fence(run, owner, generation, cancelling=True)
            self._abandon(row, 'cancelled' if row['state'] == 'cancelling' else 'failed', reason)

    def _abandon(self, row, target, reason):
        run, now = row['id'], time.time()
        uncertain = self.db.execute("SELECT 1 FROM agent_calls WHERE run_id=? AND state='started' AND classification!='read'", (run,)).fetchone()
        self.db.execute("UPDATE agent_calls SET state=CASE WHEN state='started' AND classification!='read' THEN 'uncertain' ELSE 'cancelled' END,updated_at=? WHERE run_id=? AND state IN ('started','approved','proposed','waiting')", (now, run))
        self.db.execute("UPDATE agent_turns SET state='interrupted' WHERE run_id=? AND state='started'", (run,))
        self._transition(row, 'needs_reconciliation' if uncertain else target, error='Interrupted effect requires review; it was not replayed' if uncertain else reason)

    def control(self, run, body, action):
        with self.transaction():
            def work():
                row = self._run(run)
                if action == 'cancel':
                    if row['state'] == 'running': self._transition(row, 'cancelling')
                    elif row['state'] in ('queued','waiting_for_approval','interrupted'):
                        now = time.time()
                        self.db.execute("UPDATE agent_approvals SET decision='cancelled',decided_at=?,actor='owner' WHERE run_id=? AND decision='pending'", (now, run))
                        self.db.execute("UPDATE agent_calls SET state='cancelled',updated_at=? WHERE run_id=? AND state IN ('waiting','approved','proposed')", (now, run))
                        self._transition(row, 'cancelled')
                elif action == 'resume':
                    if row['state'] != 'interrupted' or row['deadline'] <= time.time():
                        raise AgentFault('Run cannot be safely resumed', 409)
                    if self.db.execute("SELECT COUNT(*) FROM agent_runs WHERE state IN ('queued','running','waiting_for_approval','cancelling')").fetchone()[0] >= MAX_ACTIVE:
                        raise AgentFault('Agent run capacity reached', 429)
                    self._transition(row, 'queued')
                return self._public(self._run(run))
            return self._operation(body.operation_id, 'agent/' + action + '/' + run, body.model_dump(), work)

    def recover(self, *, expired_only=False):
        with self.transaction():
            self._expire()
            sql = "SELECT id FROM agent_runs WHERE state IN ('running','cancelling')"
            for result in self.db.execute(sql + (' AND lease_until<=?' if expired_only else ''), (time.time(),) if expired_only else ()).fetchall():
                row = self._run(result['id'])
                self._abandon(row, 'cancelled' if row['state'] == 'cancelling' else 'interrupted', 'Agent executor interrupted; no effect was replayed')

    def scope_current(self, row):
        if row['grant_json']['root'] != self.root: raise AgentFault('Workspace root changed', 409)
        project = row['grant_json']['project_id']
        if project:
            self.app._project(project)
            if row['scope']['kind'] == 'notebook': self.app._scope(project, row['scope']['resource_id'])
        if row['scope']['kind'] == 'notebook': return self.app._notebook_scope(row['scope']['resource_id'])


    def record_handoff(self, run, operation, created, objective):
        with self.transaction():
            row = self._fence(run['id'],run['owner'],run['generation'],cancelling=True)
            call = self.db.execute("SELECT * FROM agent_calls WHERE run_id=? AND operation_id=? AND state='started'",(row['id'],operation)).fetchone()
            if not call or call['tool']!='create_workspace_notebook': raise AgentFault('Handoff effect is not owned',409)
            review = self.db.execute("SELECT id,digest FROM agent_approvals WHERE call_id=? AND decision='granted'",(call['id'],)).fetchone()
            if not review: raise AgentFault('Handoff has no approved creation',409)
            notebook,project=created['notebook_id'],created['project_id']
            self.app._scope(project,notebook)
            destination=self._conversation(Scope(kind='notebook',resource_id=notebook))['id']
            handoff,now=identity(),time.time()
            plan={'objective':objective,'approved_creation':json.loads(call['arguments']),'approval_id':review['id'],'digest':review['digest']}
            self.db.execute('INSERT INTO agent_handoffs VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (handoff,row['id'],operation,row['conversation_id'],destination,project,notebook,created['path'],encoded(plan),now))
            self._event(row['id'],'handoff_saved',handoff_id=handoff,notebook_id=notebook,project_id=project,path=created['path'])
            return {'id':handoff,'destination_conversation_id':destination}

    def handoffs(self, conversation, limit):
        with self.app.mutex:
            self.app._row(conversation,'conversation')
            rows=self.db.execute('SELECT * FROM agent_handoffs WHERE source_conversation_id=? OR destination_conversation_id=? ORDER BY created_at DESC LIMIT ?', (conversation,conversation,limit)).fetchall()
            return {'items':[self.redactor.value({k:json.loads(r[k]) if k=='plan' else r[k] for k in r.keys() if k!='operation_id'}) for r in rows]}

    def messages(self, conversation, before, limit):
        with self.app.mutex:
            self.app._row(conversation,'conversation')
            rows=self.db.execute("SELECT * FROM records WHERE kind='message' AND parent_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?", (conversation,before or 2**63-1,limit+1)).fetchall()
            return {'items': [self.redactor.value(self.app._view(r)) for r in reversed(rows[:limit])],
                    'next_before': rows[limit-1]['sequence'] if len(rows)>limit else None}
