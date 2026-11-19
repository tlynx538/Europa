"""Durable roadmap progress for Coding Agent runs (Phase 4D Stage 3).

The harness, never the model, moves the run's roadmap task:
- start      → in_progress (explanation cleared)                         op agent-start-<run>
- verified   → completed, accepted_by='coding_agent', evidence, links     op agent-complete-<run>
               + a task_completed activity (actor coding_agent)           op agent-complete-activity-<run>
- unverified → in_progress + explanation (+ the report as evidence)       op agent-unverified-<run>
- failed / budget exhausted / interrupted → blocked + reason              op agent-blocked-<run>
- cancelled, or nothing applied (e.g. every proposal denied)
             → pending if this run added no evidence, else in_progress     op agent-reset-<run>

Owner changes always win. After each of its own writes the harness records the task's
fingerprint in a `task_progress` run event; before the next write it compares the current
task with that fingerprint and, if the owner (or anyone else) changed it meanwhile, records
`task_update_skipped` and leaves the task alone. A run with no recorded baseline (one the
harness did not start) is never touched.

Verified success reads receipts from the NotebookStore, never only tool-call results:
- code: every code cell this run wrote has, as its latest run, an applied receipt with
  status ok whose source hash equals this run's latest edit of the cell (no unexecuted
  later edit), and the cell still holds that source;
- notes only (Markdown edits, no code): every proposed edit succeeded and the cells exist.
"""
import hashlib
import json
import logging

from application import models as m
from application.store import ApplicationError
from agents.tools import EDIT_TOOLS, clip, own_cell_hash
from notebooks.store import source_hash

log = logging.getLogger(__name__)
TRACKED = ('status', 'explanation', 'accepted', 'evidence', 'links')
PROPOSED = ('succeeded', 'failed', 'uncertain', 'denied', 'expired', 'cancelled')


def fingerprint(task):
    view = {k: task.get(k) for k in TRACKED}
    view['evidence'] = [m.Evidence.model_validate(e).model_dump() for e in task.get('evidence') or []]
    view['links'] = m.Links.model_validate(task.get('links') or {}).model_dump()
    return hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode()).hexdigest()


def tracked(run):
    task = run.get('task') or {}
    return run['role'] == 'coding_agent' and bool(task.get('roadmap_id')) and not task.get('kickoff')


class TaskProgress:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def app(self):
        return self.runtime.services.app

    @property
    def store(self):
        return self.runtime.store

    # ------------------------------------------------------------------ writes
    def _read(self, run):
        record = self.app.get(run['task']['roadmap_id'], 'roadmap')
        return record, next((t for t in record['data']['tasks'] if t['id'] == run['task']['task_id']), None)

    def _committed(self, operation_id):
        with self.app.transaction():
            return self.app.db.execute('SELECT 1 FROM operations WHERE id=?', (operation_id,)).fetchone() is not None

    def _baseline(self, run_id):
        event = self.store.last_event(run_id, 'task_progress')
        return event['data'] if event else None

    def _record(self, run, task, operation_id, start_evidence):
        self.store.event(run['id'], 'task_progress', {'task_id': task['id'], 'status': task['status'], 'operation_id': operation_id,
                                                      'fingerprint': fingerprint(task), 'start_evidence': start_evidence})

    def _skip(self, run, operation_id, reason):
        self.store.event(run['id'], 'task_update_skipped', {'task_id': run['task']['task_id'], 'operation_id': operation_id,
                                                             'reason': reason})
        return 'skipped', None

    def write(self, run, operation_id, change, *, start=False, trusted=False):
        """Apply `change(raw_task) -> bool` (True if it changed something). Returns (outcome, record)."""
        for _ in range(4):
            record, task = self._read(run)
            if task is None: return self._skip(run, operation_id, 'The task is no longer in the roadmap.')
            base = None if start else self._baseline(run['id'])
            start_evidence = len(task['evidence']) if start else (base or {}).get('start_evidence', 0)
            if self._committed(operation_id):  # Replay after a crash: our write already committed.
                self._record(run, task, operation_id, start_evidence)
                return 'unchanged', record
            if not start:
                if base is None: return self._skip(run, operation_id, 'The harness did not start this task.')
                if base['fingerprint'] != fingerprint(task):
                    return self._skip(run, operation_id, 'The task changed since the Coding Agent last updated it; the change was kept.')
            raw = {k: task[k] for k in m.Task.model_fields}
            if not change(raw):
                if start: self._record(run, task, operation_id, start_evidence)
                return 'unchanged', record
            try:
                result = self.app.roadmap_update(record['id'], m.TaskUpdate(operation_id=operation_id, revision=record['revision'],
                                                 task=m.Task.model_validate(raw)), True,
                                                 trusted_acceptor='coding_agent' if trusted else None)
            except ApplicationError as exc:
                if exc.status_code == 409: continue  # stale roadmap revision: re-read (bounded)
                raise
            written = next(t for t in result['data']['tasks'] if t['id'] == task['id'])
            self._record(run, written, operation_id, start_evidence)
            return 'written', result
        return self._skip(run, operation_id, 'The roadmap kept changing; the task was not updated.')

    # ------------------------------------------------------------------ transitions
    def start(self, run):
        def change(raw):
            if raw['status'] not in ('pending', 'blocked', 'needs_input'): return False
            raw.update(status='in_progress', explanation=None)
            return True
        return self.write(run, 'agent-start-' + run['id'], change, start=True)

    def blocked(self, run, reason):
        def change(raw):
            if raw['status'] != 'in_progress': return False
            raw.update(status='blocked', explanation=clip(f'Coding Agent: {reason}', 2000))
            return True
        return self.write(run, 'agent-blocked-' + run['id'], change)

    def reset(self, run, why):
        base = self._baseline(run['id']) or {}

        def change(raw):
            if raw['status'] != 'in_progress': return False
            if len(raw['evidence']) > base.get('start_evidence', 0):
                raw.update(explanation=clip(f'Coding Agent: {why}; its evidence is kept for your review.', 2000))
            else:
                raw.update(status='pending', explanation=None)
            return True
        return self.write(run, 'agent-reset-' + run['id'], change)

    def finished(self, run, message, report):
        """A completed run: complete the task on verified success, else record what happened."""
        verified, detail = self.verify(run)
        if verified:
            evidence = {'summary': clip(detail['summary'], 2000), 'message_id': message['id'],
                        'execution_receipt_id': detail.get('receipt'), 'cell_ids': detail.get('notes', [])}

            def complete(raw):
                if raw['status'] != 'in_progress': return False
                raw['evidence'] = (raw['evidence'] + [evidence])[-32:]
                raw['links'] = {**raw['links'], 'notebook_id': run['notebook_id'], 'cell_id': detail['cell']}
                raw.update(status='completed', accepted=True, explanation=None)
                return True
            outcome, _ = self.write(run, 'agent-complete-' + run['id'], complete, trusted=True)
            if outcome in ('written', 'unchanged'):
                self.activity(run, message, detail)
            return outcome
        if not detail.get('applied'):
            return self.reset(run, 'nothing was applied')[0]
        summary = clip(f'Coding Agent report (not verified: {detail["reason"]}): {report}', 1900)

        def unverified(raw):
            if raw['status'] != 'in_progress': return False
            if len(raw['evidence']) < 32:
                raw['evidence'].append({'summary': summary, 'message_id': message['id'], 'execution_receipt_id': None})
            raw['explanation'] = clip(f'Coding Agent finished without verified success: {detail["reason"]}', 2000)
            return True
        return self.write(run, 'agent-unverified-' + run['id'], unverified)[0]

    def activity(self, run, message, detail):
        body = m.ActivityCreate(operation_id='agent-complete-activity-' + run['id'], project_id=run['project_id'],
                                notebook_id=run['notebook_id'], task_id=run['task']['task_id'], cell_id=detail['cell'],
                                execution_receipt_id=detail.get('receipt'), message_id=message['id'],
                                event_type='task_completed', result='succeeded',
                                summary=clip(f"Coding Agent completed \"{run['task']['title']}\" — {detail['summary']}", 2000))
        try:
            self.app.activity_append(run['project_id'], body, actor='coding_agent')
        except ApplicationError:
            log.exception('Could not record the task_completed activity')

    # ------------------------------------------------------------------ verification
    def verify(self, run):
        """(verified, detail). detail['applied'] is True when any edit was applied."""
        calls = self.store.tool_calls(run['id'])
        notebooks = self.runtime.services.notebooks
        document = notebooks.open(run['notebook_id'])
        cells = {c['id']: c for c in document['cells']}
        edits = [c for c in calls if c['name'] in EDIT_TOOLS]
        applied = [c for c in edits if c['state'] == 'succeeded']
        if not applied:
            return False, {'applied': False, 'reason': 'no edit was applied'}

        def target(call):
            if call['name'] == 'replace_cell_source': return call['binding']['cell_id']
            return ((call['result'] or {}).get('data') or {}).get('cell_id')

        def code(call):
            if call['name'] == 'insert_code_cell': return True
            if call['name'] == 'insert_markdown_cell': return False
            cell = cells.get(target(call))
            return cell is None or cell['cell_type'] != 'markdown'
        code_cells = list(dict.fromkeys(target(c) for c in applied if code(c)))
        if code_cells:
            receipt, primary = None, None
            for cell in code_cells:
                owned = own_cell_hash(calls, cell)
                runs = [c for c in calls if c['name'] == 'execute_cell' and c['state'] == 'succeeded' and c['arguments'].get('cell_id') == cell]
                if owned is None or not runs:
                    return False, {'applied': True, 'reason': f'cell {cell} was written but never ran successfully'}
                try:
                    found = notebooks.execution(run['notebook_id'], runs[-1]['id'])
                except Exception:
                    return False, {'applied': True, 'reason': f'the receipt for cell {cell} cannot be read'}
                if found['state'] != 'applied' or (found.get('result') or {}).get('status') != 'ok':
                    return False, {'applied': True, 'reason': f'the latest run of cell {cell} did not succeed'}
                if found['cell_id'] != cell or found['source_hash'] != owned[1]:
                    return False, {'applied': True, 'reason': f'cell {cell} was edited after its last run'}
                current = cells.get(cell)
                if current is None or source_hash(current) != owned[1]:
                    return False, {'applied': True, 'reason': f'cell {cell} changed after the Coding Agent ran it'}
                if receipt is None or runs[-1]['created_at'] >= receipt[1]:
                    receipt, primary = (found['execution_id'], runs[-1]['created_at']), cell
            return True, {'applied': True, 'receipt': receipt[0], 'cell': primary,
                          'summary': f'verified by receipt {receipt[0][:8]} (applied, status ok)'}
        proposed = [c for c in edits if c['state'] in PROPOSED and (c['result'] or {}).get('reason') != 'stale_revision']
        if any(c['state'] != 'succeeded' for c in proposed):
            return False, {'applied': True, 'reason': 'not every proposed note was applied'}
        notes = list(dict.fromkeys(target(c) for c in applied))
        if any(n not in cells for n in notes):
            return False, {'applied': True, 'reason': 'a note cell no longer exists'}
        return True, {'applied': True, 'notes': notes, 'cell': notes[-1],
                      'summary': 'verified by notes ' + ', '.join(n[:8] for n in notes)}
