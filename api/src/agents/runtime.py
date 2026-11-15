"""AgentRuntime: the durable, backend-owned agent loop.

The browser only creates runs, decides approvals and cancels. A run's progress
is a sequence of persisted steps; every resume rebuilds model input from them, so
a refresh, a lost response or a restart never re-generates an approved action.
"""
import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Callable
import uuid

from pydantic import ValidationError

from application import models as m
from application.store import ApplicationError
from agents.context import assemble
from agents.policy import APPROVAL_TTL_SECONDS, MAX_CONCURRENT_RUNS, MAX_DELEGATION_DEPTH, ROLES, Budget, PolicyDenied, PolicyEngine
from agents.providers import ProviderError
from agents.schema import TERMINAL_RUN_STATES
from agents.store import AgentStore, LostOwnership
from agents.tools import REGISTRY, ToolContext, ToolError, _notebook_roadmap, _project_for, clip, digest

log = logging.getLogger(__name__)
SIDE_EFFECT_FREE = {'read'}


class BudgetExhausted(Exception):
    pass


@dataclass
class Services:
    app: Any
    notebooks: Any
    workspace: Any
    mutation: Any
    notebook_service: Callable[[], Any]
    runtime: Any = None


def _model_content(result):
    body = {'status': result.get('status', 'ok'), 'summary': result.get('summary', '')}
    if 'data' in result: body['data'] = result['data']
    return clip(json.dumps(body, ensure_ascii=False, default=str), 8000)


def _public(result):
    return {k: result[k] for k in ('status', 'summary', 'links') if k in result}


class AgentRuntime:
    def __init__(self, services, provider, *, policy=None, max_concurrent=MAX_CONCURRENT_RUNS,
                 approval_ttl=APPROVAL_TTL_SECONDS, model_timeout=120.0, poll_interval=0.5):
        self.services, self.provider = services, provider
        services.runtime = self
        self.store = AgentStore(services.app)
        self.policy = policy or PolicyEngine()
        self.owner = uuid.uuid4().hex
        self.max_concurrent, self.approval_ttl = max_concurrent, approval_ttl
        self.model_timeout, self.poll_interval = model_timeout, poll_interval
        self.tasks: dict[str, asyncio.Task] = {}
        self._wake = asyncio.Event()
        self._scheduler_task = None
        self.closed = False

    # ------------------------------------------------------------------ lifecycle
    def status(self):
        return {'enabled': True, 'provider': self.provider.name, 'model': self.provider.model, 'reason': None}

    async def start(self):
        await self.recover()
        self._scheduler_task = asyncio.create_task(self._scheduler())

    async def close(self, grace=10.0):
        self.closed = True
        self._wake.set()
        if self._scheduler_task:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)
        running = tuple(self.tasks.values())
        if running:
            # A cancelled worker leaves the run 'running'; restart recovery reconciles it.
            _, pending = await asyncio.wait(running, timeout=grace)
            for task in pending: task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
        await self.provider.close()

    def wake(self):
        self._wake.set()

    async def _scheduler(self):
        while not self.closed:
            try:
                await asyncio.to_thread(self.store.expire_approvals)
                free = self.max_concurrent - len(self.tasks)
                if free > 0:
                    for run_id in await asyncio.to_thread(self.store.queued, free):
                        if run_id not in self.tasks:
                            task = asyncio.create_task(self._run(run_id))
                            self.tasks[run_id] = task
                            task.add_done_callback(lambda _t, r=run_id: (self.tasks.pop(r, None), self.wake()))
            except Exception:  # Storage hiccup: keep scheduling; runs stay durable.
                log.exception('Agent scheduler iteration failed')
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def idle(self, timeout=30.0):
        """Test helper: wait until no run is queued or running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active = await asyncio.to_thread(self.store.active_count)
            queued = await asyncio.to_thread(self.store.queued, 1)
            if not active and not queued and not self.tasks: return
            await asyncio.sleep(0.05)
        raise TimeoutError('Agent runtime did not become idle')

    # ------------------------------------------------------------------ public operations
    def _scope_for(self, conversation_id, message_id):
        app = self.services.app
        conversation = app.get(conversation_id, 'conversation')
        with app.transaction():
            if app._workspace_conversation(conversation_id):
                role, notebook, project = 'home_coordinator', None, None
            else:
                canonical = app.db.execute('SELECT notebook_id FROM canonical_notebook_conversations WHERE conversation_id=?',
                                           (conversation_id,)).fetchone()
                if canonical is None:
                    raise ApplicationError('Agents run only in Home or notebook conversations', 409)
                role, notebook = 'notebook_coordinator', canonical['notebook_id']
                membership = app.db.execute('SELECT project_id FROM memberships WHERE notebook_id=? AND associated=1',
                                            (notebook,)).fetchone()
                project = membership['project_id'] if membership else None
            message = app._row(message_id, 'message')
            data = json.loads(message['data'])
            if message['parent_id'] != conversation['id']:
                raise ApplicationError('Message belongs to another conversation', 422)
            if data['author_role'] != 'user':
                raise ApplicationError('Agent runs respond only to owner messages', 422)
        return role, notebook, project

    async def start_run(self, *, operation_id, conversation_id, message_id):
        role, notebook, project = await asyncio.to_thread(self._scope_for, conversation_id, message_id)
        budget = ROLES[role].budget.as_dict()
        run, created = await asyncio.to_thread(
            self.store.create_run, operation_id=operation_id, payload={'conversation': conversation_id, 'message': message_id},
            role=role, conversation_id=conversation_id, trigger_message_id=message_id, notebook_id=notebook,
            project_id=project, parent_run_id=None, depth=0, task=None, provider=self.provider.name,
            model=self.provider.model, budget=budget)
        if created: self.wake()
        return run

    async def decide(self, approval_id, *, operation_id, decision, digest_value):
        approval, changed = await asyncio.to_thread(self.store.decide, approval_id, operation_id=operation_id,
                                                    decision=decision, digest_value=digest_value)
        if changed: self.wake()
        return approval

    async def cancel(self, run_id):
        run = await asyncio.to_thread(self.store.request_cancel, run_id)
        self.wake()
        return run

    async def record_progress(self, ctx, partial):
        await asyncio.to_thread(self.store.merge_call_result, ctx.call_id, ctx.run['id'], ctx.run['_lease'], partial)

    def _task_update(self, roadmap_id, task_id, operation_id, change):
        app = self.services.app
        for _ in range(3):
            record = app.get(roadmap_id, 'roadmap')
            task = next((t for t in record['data']['tasks'] if t['id'] == task_id), None)
            if task is None: return None
            raw = {k: task[k] for k in m.Task.model_fields}
            change(raw)
            try:
                return app.roadmap_update(roadmap_id, m.TaskUpdate(operation_id=operation_id, revision=record['revision'],
                                                                  task=m.Task.model_validate(raw)), True)
            except ApplicationError as exc:
                if exc.status_code != 409: raise
        return None

    async def delegate(self, parent, task_id, instructions, *, operation_id):
        if parent['depth'] + 1 > MAX_DELEGATION_DEPTH:
            raise ToolError('Delegation depth limit reached', 'policy')

        def validate():
            ctx = ToolContext(run=parent, services=self.services)
            project = _project_for(ctx)
            roadmap = _notebook_roadmap(ctx, project)
            if roadmap is None: raise ToolError('No roadmap exists for this notebook', 'conflict')
            tasks = {t['id']: t for t in roadmap['data']['tasks']}
            task = tasks.get(task_id)
            if task is None: raise ToolError(f'Task {task_id} is not in the approved roadmap', 'conflict')
            if task['status'] in {'completed', 'cancelled'}: raise ToolError(f"Task {task_id} is {task['status']}", 'conflict')
            blocking = [d for d in task['dependencies'] if tasks[d]['status'] != 'completed']
            if blocking: raise ToolError(f"Task {task_id} depends on unfinished tasks: {', '.join(blocking)}", 'conflict')
            active = self.store.active_child(parent['notebook_id'])
            if active: raise ToolError(f'A Coding Agent run ({active[:8]}) is already active in this notebook', 'conflict')
            return roadmap, task
        roadmap, task = await asyncio.to_thread(validate)
        brief = {'roadmap_id': roadmap['id'], 'task_id': task_id, 'title': task['title'],
                 'acceptance_criteria': task['acceptance_criteria'], 'instructions': instructions}
        child, created = await asyncio.to_thread(
            self.store.create_run, operation_id=operation_id,
            payload={'parent': parent['id'], 'task': task_id, 'instructions': instructions},
            role='coding_agent', conversation_id=parent['conversation_id'], trigger_message_id=parent['trigger_message_id'],
            notebook_id=parent['notebook_id'], project_id=parent['project_id'], parent_run_id=parent['id'],
            depth=parent['depth'] + 1, task=brief, provider=self.provider.name, model=self.provider.model,
            budget=ROLES['coding_agent'].budget.as_dict(), event={'parent_run_id': parent['id'], 'task_id': task_id})
        if created:
            def start(raw):
                if raw['status'] in {'pending', 'blocked', 'needs_input'}:
                    raw.update(status='in_progress', explanation=None)
            await asyncio.to_thread(self._task_update, roadmap['id'], task_id, 'agent-start-' + child['id'], start)
            self.wake()
        return child

    # ------------------------------------------------------------------ worker
    async def _run(self, run_id):
        run = await asyncio.to_thread(self.store.claim, run_id, self.owner)
        if run is None: return
        lease = (self.owner, run['fence'])
        run['_lease'] = lease
        usage = run['usage']
        started = time.monotonic()

        def snapshot():
            return {**usage, 'active_seconds': round(usage['active_seconds'] + time.monotonic() - started, 3)}
        try:
            await self._drive(run, lease, usage, snapshot)
        except LostOwnership:
            return
        except BudgetExhausted as exc:
            await self._fail(run, lease, snapshot(), f'Budget exhausted: {exc}', 'budget_exhausted')
        except asyncio.CancelledError:
            raise  # Shutdown: the run stays 'running' and is reconciled on restart.
        except ProviderError as exc:
            await self._fail(run, lease, snapshot(), str(exc), 'run_failed')
        except Exception as exc:
            log.exception('Agent run %s failed', run_id)
            await self._fail(run, lease, snapshot(), f'Internal harness error ({type(exc).__name__})', 'run_failed')

    async def _fail(self, run, lease, usage, message, event):
        try:
            await asyncio.to_thread(self.store.finish, run['id'], lease, 'failed', error=message, usage=usage,
                                    event=(event, {'error': message}))
        except LostOwnership:
            return
        if run['role'] == 'coding_agent' and run.get('task'):
            def block(raw):
                if raw['status'] == 'in_progress':
                    raw.update(status='blocked', explanation=clip(f'Coding Agent run failed: {message}', 2000))
            try:
                await asyncio.to_thread(self._task_update, run['task']['roadmap_id'], run['task']['task_id'], 'agent-blocked-' + run['id'], block)
            except Exception:
                log.exception('Could not mark task blocked')

    def _trigger_text(self, run):
        if run['role'] == 'coding_agent':
            task = run['task']
            return (f"Task {task['task_id']}: {task['title']}\nAcceptance criteria:\n- " + '\n- '.join(task['acceptance_criteria'])
                    + f"\n\nInstructions: {task['instructions']}")
        message = self.services.app.get(run['trigger_message_id'], 'message')
        text = message['data']['content']
        if message['data'].get('cell_id'):
            text += f"\n\n(The owner attached cell {message['data']['cell_id']}.)"
        return text

    @staticmethod
    def _messages(steps):
        messages = []
        for step in steps:
            if step['kind'] == 'user':
                messages += [{'role': 'system', 'content': step['system']}, {'role': 'user', 'content': step['content']}]
            elif step['kind'] == 'assistant':
                messages.append({'role': 'assistant', 'content': step['content'], 'tool_calls': step['tool_calls']})
            else:
                messages.append({'role': 'tool', 'tool_call_id': step['tool_call_id'], 'content': step['content']})
        return messages

    async def _drive(self, run, lease, usage, snapshot):
        role = self.policy.role(run['role'])
        budget = Budget(**run['budget'])
        specs = [tool.spec() for tool in self.policy.tools_for(run['role'])]
        steps = await asyncio.to_thread(self.store.steps, run['id'])
        if not steps:
            system, user, provenance = await asyncio.to_thread(
                assemble, self.services, run, await asyncio.to_thread(self._trigger_text, run), budget_chars=budget.context_chars)
            await asyncio.to_thread(self.store.add_step, run['id'], lease, 'user',
                                    {'system': system, 'content': user, 'provenance': provenance},
                                    event=('context_assembled', {'sources': provenance}))
        while True:
            if await self._stop_if_cancelled(run, lease, snapshot):
                return
            if snapshot()['active_seconds'] > budget.active_seconds:
                raise BudgetExhausted('active runtime')
            steps = await asyncio.to_thread(self.store.steps, run['id'])
            if len(steps) > budget.steps: raise BudgetExhausted('steps')
            last = next((s for s in reversed(steps) if s['kind'] == 'assistant'), None)
            answered = {s['tool_call_id'] for s in steps if s['kind'] == 'tool'}
            pending = [c for c in (last or {}).get('tool_calls', []) if c['id'] not in answered]
            if pending:
                supervised = False
                for call in pending:
                    outcome = await self._handle_call(run, lease, last['idx'], call, usage, budget, supervised)
                    if outcome == 'wait':
                        await asyncio.to_thread(self.store.finish, run['id'], lease, 'waiting_for_approval', usage=snapshot(),
                                                event=('waiting_for_approval', {}))
                        return
                    supervised = supervised or outcome == 'supervised'
                continue
            if last is not None and not last['tool_calls'] and steps[-1]['kind'] == 'assistant':
                await self._complete(run, lease, last['content'], snapshot())
                return
            if usage['model_calls'] >= budget.model_calls: raise BudgetExhausted('model requests')
            try:
                response = await asyncio.wait_for(self.provider.complete(self._messages(steps), specs, max_tokens=budget.max_output_tokens),
                                                  timeout=self.model_timeout)
            except asyncio.TimeoutError:
                raise ProviderError('Model request timed out') from None
            usage['model_calls'] += 1
            for key in ('prompt_tokens', 'completion_tokens'):
                usage[key] += response.usage.get(key, 0)
            if await self._stop_if_cancelled(run, lease, snapshot):
                return  # A response that arrives after cancellation is discarded, never published.
            calls = [{'id': c.id, 'name': c.name, 'arguments': c.arguments} for c in response.tool_calls[:8]]
            content = clip(response.content or '', 8000)
            note = ('agent_note', {'text': clip(content, 1000)}) if content.strip() and calls else None
            await asyncio.to_thread(self.store.add_step, run['id'], lease, 'assistant', {'content': content, 'tool_calls': calls},
                                    usage=snapshot(), event=note)
            if not calls:
                await self._complete(run, lease, content, snapshot())
                return

    async def _stop_if_cancelled(self, run, lease, snapshot):
        if not await asyncio.to_thread(self.store.cancel_requested, run['id']):
            return False
        await asyncio.to_thread(self.store.finish, run['id'], lease, 'cancelled', usage=snapshot(),
            event=('run_cancelled', {'note': 'Stopped before further agent work. Committed edits or executions are not undone.'}))
        return True

    async def _record(self, run, lease, step_idx, call, name, state, result, **extra):
        row = await asyncio.to_thread(self.store.add_tool_call, run['id'], lease, step_idx=step_idx, provider_call_id=call['id'],
                                      name=name, version=extra.pop('version', 0), arguments=extra.pop('arguments', {}),
                                      state=state, result=result, event=('tool_rejected', {'tool': name, **_public(result)}), **extra)
        await asyncio.to_thread(self.store.add_step, run['id'], lease, 'tool', {'tool_call_id': call['id'], 'content': _model_content(result)})
        return row

    async def _handle_call(self, run, lease, step_idx, call, usage, budget, supervised):
        existing = await asyncio.to_thread(self.store.tool_call, run['id'], step_idx, call['id'])
        if existing:
            tool = REGISTRY.get(existing['name'])
            state = existing['state']
            if state == 'awaiting_approval':
                return 'wait'
            if state == 'approved':
                await self._execute(run, lease, tool, existing, usage)
                return 'supervised' if tool.approval else 'done'
            if state in {'denied', 'expired', 'cancelled'}:
                verb = {'denied': 'denied by the owner', 'expired': 'expired before an owner decision',
                        'cancelled': 'cancelled'}[state]
                result = {'status': state, 'summary': f'The proposed {existing["name"]} was {verb}. Nothing was changed.'}
                await asyncio.to_thread(self.store.set_call, existing['id'], run['id'], lease, state=state, result=result,
                                        event=('tool_result', {'tool': existing['name'], **_public(result)}))
                await asyncio.to_thread(self.store.add_step, run['id'], lease, 'tool', {'tool_call_id': call['id'], 'content': _model_content(result)})
                return 'done'
            if state == 'executing':
                raise RuntimeError('Tool call left executing outside recovery')
            result = existing['result'] or {'status': state, 'summary': state}
            await asyncio.to_thread(self.store.add_step, run['id'], lease, 'tool', {'tool_call_id': call['id'], 'content': _model_content(result)})
            return 'done'
        usage['tool_calls'] += 1
        if usage['tool_calls'] > budget.tool_calls: raise BudgetExhausted('tool calls')
        try:
            tool = self.policy.check(run, call['name'])
        except PolicyDenied as exc:
            await self._record(run, lease, step_idx, call, call['name'][:64] or 'unknown', 'rejected',
                               {'status': 'policy', 'summary': f'Not permitted: {exc}'})
            return 'done'
        try:
            raw = json.loads(call['arguments'] or '{}')
            if not isinstance(raw, dict): raise ValueError('arguments must be an object')
            args = tool.args.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            detail = clip(str(exc), 1500)
            await self._record(run, lease, step_idx, call, tool.name, 'rejected', {'status': 'invalid', 'summary': f'Invalid arguments: {detail}'},
                               version=tool.version)
            return 'done'
        arguments = args.model_dump(mode='json')
        identity = uuid.uuid4().hex
        ctx = ToolContext(run=run, services=self.services, call_id=identity, operation_id='agent-' + identity)
        if tool.approval:
            if supervised:
                await self._record(run, lease, step_idx, call, tool.name, 'rejected',
                                   {'status': 'policy', 'summary': 'Only one supervised action can be proposed per step. Propose it again after this one settles.'},
                                   version=tool.version, arguments=arguments)
                return 'done'
            if run['role'] == 'coding_agent' and usage['repairs'] > budget.repairs:
                await self._record(run, lease, step_idx, call, tool.name, 'rejected',
                                   {'status': 'policy', 'summary': 'Repair budget exhausted. Stop and report the failure to the Coordinator.'},
                                   version=tool.version, arguments=arguments)
                return 'done'
            try:
                binding = await tool.bind(ctx, args)
            except ToolError as exc:
                await self._record(run, lease, step_idx, call, tool.name, 'rejected', {'status': exc.kind, 'summary': str(exc)},
                                   version=tool.version, arguments=arguments)
                return 'done'
            preview = tool.preview(ctx, args, binding)
            value = digest(tool, arguments, binding, run['id'])
            await asyncio.to_thread(self.store.add_tool_call, run['id'], lease, step_idx=step_idx, provider_call_id=call['id'],
                name=tool.name, version=tool.version, arguments=arguments, binding=binding, digest_value=value,
                state='awaiting_approval', identity=identity,
                approval={'preview': preview, 'notebook_id': binding.get('notebook_id') or run.get('notebook_id'),
                          'project_id': binding.get('project_id') or run.get('project_id'),
                          'revision': binding.get('revision', binding.get('roadmap_revision')), 'ttl': self.approval_ttl},
                event=('approval_requested', {'tool': tool.name, 'title': preview.get('title', tool.name)}))
            return 'wait'
        row = await asyncio.to_thread(self.store.add_tool_call, run['id'], lease, step_idx=step_idx, provider_call_id=call['id'],
            name=tool.name, version=tool.version, arguments=arguments, state='executing', identity=identity,
            event=('tool_started', {'tool': tool.name}))
        await self._execute(run, lease, tool, row, usage, started=False)
        return 'done'

    async def _execute(self, run, lease, tool, call, usage, started=True):
        args = tool.args.model_validate(call['arguments'])
        ctx = ToolContext(run=run, services=self.services, call_id=call['id'], operation_id=call['operation_id'])
        if call['state'] != 'executing':
            # Durable intent before the side effect: the reconciliation anchor.
            await asyncio.to_thread(self.store.set_call, call['id'], run['id'], lease, state='executing', started=True,
                                    event=('tool_started', {'tool': tool.name}))
        side_effect = tool.idempotency not in SIDE_EFFECT_FREE
        try:
            result = await asyncio.wait_for(tool.execute(ctx, args, call['binding']), timeout=tool.timeout)
            state = 'succeeded'
        except ToolError as exc:
            result, state = {'status': exc.kind, 'summary': str(exc)}, 'failed'
        except asyncio.TimeoutError:
            result = {'status': 'uncertain' if side_effect else 'error',
                      'summary': f'{tool.name} did not settle within {int(tool.timeout)}s' + ('; its outcome is unknown and it will not be retried automatically' if side_effect else '')}
            state = 'uncertain' if side_effect else 'failed'
        except (ApplicationError, OSError) as exc:
            result = {'status': 'uncertain' if side_effect else 'error', 'summary': f'{tool.name} failed: {clip(str(exc), 300)}'}
            state = 'uncertain' if side_effect else 'failed'
        result.setdefault('status', 'ok')
        if tool.name == 'execute_cell' and result.get('status') == 'error' and state == 'succeeded':
            usage['repairs'] += 1
        partial = (await asyncio.to_thread(self.store.tool_call, run['id'], call['step_idx'], call['provider_call_id']))['result'] or {}
        result = {**{k: v for k, v in partial.items() if k == 'execution_id'}, **result}
        await asyncio.to_thread(self.store.set_call, call['id'], run['id'], lease, state=state, result=result,
                                event=('tool_result', {'tool': tool.name, **_public(result)}))
        await asyncio.to_thread(self.store.add_step, run['id'], lease, 'tool',
                                {'tool_call_id': call['provider_call_id'], 'content': _model_content(result)})

    async def _complete(self, run, lease, content, usage):
        role = self.policy.role(run['role'])
        content = content.strip() or '(No reply was produced.)'
        calls = await asyncio.to_thread(self.store.tool_calls, run['id'])
        links = {}
        for call in calls:
            if call['state'] == 'succeeded':
                for key, value in ((call['result'] or {}).get('links') or {}).items():
                    links[key] = value
        message_links = {}
        if run['role'] == 'home_coordinator' and links.get('notebook_id'):
            message_links['notebook_id'] = links['notebook_id']
        if run['role'] == 'coding_agent':
            if links.get('execution_receipt_id'):
                message_links.update(execution_receipt_id=links['execution_receipt_id'], cell_id=links.get('cell_id'))
            elif links.get('cell_id'):
                message_links['cell_id'] = links['cell_id']
        if run['role'] == 'coding_agent' and run.get('task'): message_links['task_id'] = run['task']['task_id']

        def post():
            body = dict(operation_id='agent-reply-' + run['id'], content=clip(content, 30000), **{k: v for k, v in message_links.items() if v})
            try:
                return self.services.app.message_append(run['conversation_id'], m.MessageCreate(**body), author_role=role.author)
            except ApplicationError as exc:
                if exc.status_code not in {404, 409, 422}: raise
                # A referenced cell/receipt/task may have changed; the reply itself must still persist.
                return self.services.app.message_append(run['conversation_id'], m.MessageCreate(
                    operation_id='agent-reply-plain-' + run['id'], content=clip(content, 30000)), author_role=role.author)
        message = await asyncio.to_thread(post)
        if run['role'] == 'coding_agent' and run.get('task'):
            summary = clip(f'Coding Agent report: {content}', 1900)
            receipt = links.get('execution_receipt_id')

            def evidence(raw):
                if len(raw['evidence']) < 32:
                    raw['evidence'].append({'summary': summary, 'message_id': message['id'],
                                            'execution_receipt_id': receipt})
            try:
                await asyncio.to_thread(self._task_update, run['task']['roadmap_id'], run['task']['task_id'], 'agent-evidence-' + run['id'], evidence)
            except Exception:
                log.exception('Could not record task evidence')
        await asyncio.to_thread(self.store.finish, run['id'], lease, 'completed', usage=usage,
                                result={'message_id': message['id'], 'links': message_links},
                                event=('reply_posted', {'message_id': message['id'], 'author': role.author}))

    # ------------------------------------------------------------------ recovery
    async def recover(self):
        """Reconcile runs a previous control plane left running. Never blindly replays effects."""
        for stale in await asyncio.to_thread(self.store.unowned_active_runs):
            run = await asyncio.to_thread(self.store.recover_claim, stale['id'], self.owner)
            lease = (self.owner, run['fence'])
            run['_lease'] = lease
            uncertain = []
            for call in await asyncio.to_thread(self.store.tool_calls, run['id'], 'executing'):
                tool = REGISTRY[call['name']]
                ctx = ToolContext(run=run, services=self.services, call_id=call['id'], operation_id=call['operation_id'])
                if tool.idempotency in SIDE_EFFECT_FREE or tool.reconcile is None:
                    outcome, result = 'not_applied', None  # read-only, or operation-ID idempotent creation
                else:
                    partial = {**(call['result'] or {}), 'started_at': call['started_at'] or call['created_at']}
                    try:
                        outcome, result = await tool.reconcile(ctx, tool.args.model_validate(call['arguments']), call['binding'], partial)
                    except Exception as exc:
                        outcome, result = 'uncertain', {'summary': f'Reconciliation failed ({type(exc).__name__}); not retried'}
                if outcome == 'not_applied':
                    await asyncio.to_thread(self.store.set_call, call['id'], run['id'], lease, state='approved',
                                            event=('tool_reconciled', {'tool': call['name'], 'outcome': 'not_applied'}))
                    continue
                state = {'succeeded': 'succeeded', 'failed': 'failed'}.get(outcome, 'uncertain')
                result = {'status': (result or {}).get('status', 'ok' if state == 'succeeded' else state), **(result or {})}
                await asyncio.to_thread(self.store.set_call, call['id'], run['id'], lease, state=state, result=result,
                                        event=('tool_reconciled', {'tool': call['name'], 'outcome': state, **_public(result)}))
                await asyncio.to_thread(self.store.add_step, run['id'], lease, 'tool',
                                        {'tool_call_id': call['provider_call_id'], 'content': _model_content(result)})
                if state == 'uncertain': uncertain.append(call['name'])
            if run['cancel_requested']:
                await asyncio.to_thread(self.store.finish, run['id'], lease, 'cancelled',
                                        event=('run_cancelled', {'note': 'Cancelled during restart recovery.'}))
            elif uncertain:
                await asyncio.to_thread(self.store.finish, run['id'], lease, 'interrupted', error='Uncertain side effect after restart',
                                        event=('run_interrupted', {'reason': 'A side effect could not be verified after restart; it was not retried.',
                                                                   'tools': uncertain}))
            else:
                await asyncio.to_thread(self.store.update, run['id'], lease, state='queued', owner=None,
                                        event=('run_recovered', {'note': 'Resumed after server restart from persisted steps.'}))
