"""Bounded durable execution loop; browser waiters never own accepted work."""
import asyncio
import inspect
import time

from agents.context import ContextManager, bounded
from agents.home import HomeTools, PartialEffect, UncertainEffect
from agents.models import ModelReply
from agents.providers import DisabledProvider, ProviderFailure, Redactor, configured_provider
from agents.store import AgentFault, AgentStore, FenceLost, digest, identity
from agents.tools import PolicyEngine, ReadTools, ToolRegistry
from notebooks.store import NotebookError
from workspace.browser import WorkspaceError


class AgentRuntime:
    def __init__(self, state, provider=None, *, gateway_token=''):
        self.configuration_error = None
        if provider is None:
            try: provider = configured_provider()
            except ValueError:
                provider = DisabledProvider()
                self.configuration_error = 'Agent provider configuration is invalid'
        self.provider = provider
        self.redactor = Redactor((provider.config.api_key, gateway_token))
        self.store = AgentStore(state.application_store, state.workspace, self.redactor)
        self.context = ContextManager(self.store)
        self.policy = PolicyEngine(self.store)
        self.registry = ToolRegistry([*ReadTools(self.store).declarations(), HomeTools(self.store,state).declaration()])
        self.owner, self.wake = identity(), asyncio.Event()
        self.worker, self.closing = None, False
        self.inflight = set()

    def config(self):
        return {**self.redactor.value(self.provider.config.public()), 'error': self.configuration_error}

    async def start(self):
        await asyncio.to_thread(self.store.recover)
        if self.config()['enabled']: self.worker = asyncio.create_task(self._work(), name='europa-agent-worker')

    async def close(self):
        self.closing = True
        self.wake.set()
        if self.worker: await self.worker
        if self.inflight: await asyncio.gather(*tuple(self.inflight), return_exceptions=True)
        await asyncio.to_thread(self.store.recover)

    async def create(self, body):
        if self.closing: raise AgentFault('Agent worker is closing', 503)
        result = await asyncio.to_thread(self.store.create, body, self.config())
        self.wake.set()
        return result

    async def decide(self, run, approval, body):
        result = await asyncio.to_thread(self.store.decide, run, approval, body)
        self.wake.set()
        return result

    async def control(self, run, body, action):
        if action == 'resume' and not self.config()['enabled']: raise AgentFault('Agent provider is not configured', 503)
        result = await asyncio.to_thread(self.store.control, run, body, action)
        self.wake.set()
        return result

    async def _heartbeat(self, key):
        while True:
            await asyncio.sleep(5)
            try: await asyncio.to_thread(self.store.renew, *key)
            except FenceLost: return

    async def _work(self):
        while not self.closing:
            self.wake.clear()
            try:
                await asyncio.to_thread(self.store.recover, expired_only=True)
                row = await asyncio.to_thread(self.store.claim, self.owner)
            except AgentFault:
                if self.closing: break
                await self._wait()
                continue
            if row is None:
                await self._wait()
                continue
            key = row['id'], self.owner, row['generation']
            heartbeat = asyncio.create_task(self._heartbeat(key))
            try:
                await self._advance(key, row)
            except FenceLost:
                # Ownership is checked again after every await. A stale executor
                # never records success, dispatches a new action or replays effects.
                try: await asyncio.to_thread(self.store.fail, *key, 'Agent stopped before the next step')
                except AgentFault: pass
            except Exception as exc:
                reason = str(exc) if isinstance(exc, AgentFault) else 'Model request failed' if isinstance(exc, ProviderFailure) else 'Agent operation failed safely'
                try: await asyncio.to_thread(self.store.fail, *key, reason)
                except AgentFault: pass
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                if self.inflight: await asyncio.gather(*tuple(self.inflight), return_exceptions=True)

    async def _wait(self):
        try: await asyncio.wait_for(self.wake.wait(), 1)
        except TimeoutError: pass

    async def _advance(self, key, row):
        if (row['provider'],row['model']) != (self.config()['provider'],self.config()['model']):
            raise AgentFault('Provider configuration changed since the run was queued', 409)
        approved = await asyncio.to_thread(self.store.approved, *key)
        if approved and not self.closing: await self._invoke(key, row, approved)
        while not self.closing:
            row = await asyncio.to_thread(self.store.inspect, *key)
            if row['state'] == 'cancelling':
                await asyncio.to_thread(self.store.finish, *key, '')
                return
            definitions = self.registry.available(row) if self.config()['supports_tools'] else []
            messages, cost = await asyncio.to_thread(self.context.build, row, definitions)
            number, output = await asyncio.to_thread(self.store.model_start, *key, messages, cost)
            try:
                remaining = min(row['deadline']-time.time(), self.provider.config.timeout)
                async with asyncio.timeout(max(0, remaining)):
                    reply = ModelReply.model_validate(await self.provider.complete(messages, definitions, output))
            except (TimeoutError, ValueError) as exc:
                raise ProviderFailure('Model request failed') from exc
            if not await asyncio.to_thread(self.store.model_done, *key, number, reply, cost, output): return
            if self.closing: return
            if not reply.calls:
                await asyncio.to_thread(self.store.finish, *key, reply.content)
                return
            if not self.config()['supports_tools']: raise AgentFault('Configured model cannot use tools', 403)
            call = reply.calls[0]
            tool, args = self.registry.validate(call.name, call.arguments)
            target = await asyncio.to_thread(self.policy.authorize, row, tool, args)
            persisted = await asyncio.to_thread(self.store.propose, *key, number, call, tool, args.model_dump(), target)
            if tool.approval: return
            await self._invoke(key, row, persisted)

    def _read(self, run, tool, args, operation):
        # Membership/path policy and the read use the same lock scope.
        with self.store.app.notebooks.locked(), self.store.app.mutex:
            self.policy.authorize(run, tool, args)
            return tool.handler(run, args, operation)

    async def _invoke(self, key, row, call):
        tool, args = self.registry.validate(call['tool'], call['arguments'])
        target = await asyncio.to_thread(self.policy.authorize, row, tool, args)
        if tool.version != call['version'] or digest([row['id'], tool.name, tool.version, target, args.model_dump()]) != call['digest']:
            raise AgentFault('Approved tool contract or target changed', 409)
        await asyncio.to_thread(self.store.begin_call, *key, call['id'])
        if inspect.iscoroutinefunction(tool.handler):
            operation = tool.handler(row, args, call['operation_id'])
        elif tool.classification == 'read':
            operation = asyncio.to_thread(self._read, row, tool, args, call['operation_id'])
        else:
            operation = asyncio.to_thread(tool.handler, row, args, call['operation_id'])
        task = asyncio.create_task(operation)
        self.inflight.add(task)
        task.add_done_callback(self.inflight.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            result = await asyncio.wait_for(asyncio.shield(task), min(tool.timeout, max(0.01, row['deadline']-time.time())))
            validated = tool.result.model_validate(result).model_dump()
            safe = bounded(self.redactor.value(validated), 12000)
            await asyncio.to_thread(self.store.end_call, *key, call['id'], 'completed', result=safe)
        except FenceLost: raise
        except PartialEffect as exc:
            await asyncio.to_thread(self.store.end_call, *key, call['id'], 'failed', result=self.redactor.value(exc.result), error=str(exc))
        except (TimeoutError, UncertainEffect):
            await asyncio.to_thread(self.store.end_call, *key, call['id'], 'failed' if tool.classification=='read' else 'uncertain', error='Tool outcome could not be verified')
        except (AgentFault, NotebookError, WorkspaceError) as exc:
            definitive = exc.status_code < 500
            await asyncio.to_thread(self.store.end_call, *key, call['id'], 'failed' if definitive or tool.classification=='read' else 'uncertain',
                                    error='Tool target unavailable, invalid, or changed')
        except Exception:
            await asyncio.to_thread(self.store.end_call, *key, call['id'], 'failed' if tool.classification=='read' else 'uncertain', error='Tool result unavailable')
