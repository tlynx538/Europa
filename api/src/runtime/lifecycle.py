"""Single-owner activity journal and bounded idle retirement; no browser liveness inference."""
import asyncio
from dataclasses import dataclass
import hashlib
import math
import os
import secrets
import time

from kernel.gateway_client import GatewayError
from runtime.contracts import RuntimeErrorBase


@dataclass(frozen=True)
class IdlePolicy:
    enabled: bool = True
    timeout: float = 1800
    grace: float = 30
    interval: float = 30

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError('Idle enabled must be boolean')
        for name in ('timeout', 'grace', 'interval'):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < (0 if name == 'grace' else .01):
                raise ValueError('Invalid idle policy duration')

    @classmethod
    def environment(cls):
        enabled = os.environ.get('EUROPA_IDLE_CLEANUP', '1')
        if enabled not in {'0', '1'}:
            raise ValueError('EUROPA_IDLE_CLEANUP must be 0 or 1')
        return cls(enabled == '1', float(os.environ.get('EUROPA_IDLE_TIMEOUT_SECONDS', '1800')),
                   float(os.environ.get('EUROPA_IDLE_GRACE_SECONDS', '30')),
                   float(os.environ.get('EUROPA_IDLE_INTERVAL_SECONDS', '30')))


class ActivityGateway:
    """Preserve Gateway methods; mutations enter durable protection before Gateway I/O."""
    mutations = {'create_kernel', 'execute', 'restart_kernel', 'terminate_kernel', 'interrupt_kernel'}

    def __init__(self, lifecycle, notebook, identity, client):
        self.lifecycle, self.notebook, self.identity, self.client = lifecycle, notebook, identity, client

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in self.mutations:
            return method

        async def protected(*args, **kwargs):
            return await self.lifecycle.run(self.notebook, self.identity, self.client, name, args, kwargs)
        return protected


class RuntimeLifecycle:
    def __init__(self, supervisor, policy=None, *, clock=time.time):
        self.s = supervisor
        self.policy = policy or IdlePolicy()
        self.clock = clock
        self.tasks = set()
        self.worker = None
        self.closed = False
        self.last_error = None
        self.measurements = []

    def start(self):
        if self.policy.enabled and self.worker is None:
            self.worker = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            try:
                await asyncio.sleep(self.policy.interval)
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = 'Idle cleanup failed; inspect recovery diagnostics and reservations'

    def gateway(self, notebook, identity, client):
        self.s.registry.activity(identity, self.clock())
        return ActivityGateway(self, notebook, identity, client)

    def _row(self, name, identity):
        row = self.s.registry.get(identity)
        if (self.closed or self.s._closing or self.s.notebooks.get(name) != identity or not row
                or row['notebook_id'] != name or row['kind'] != 'assigned' or row['phase'] != 'active'
                or self.s._generations.get(identity) != row['generation']):
            raise RuntimeErrorBase('Runtime is not available for notebook activity')
        return row

    async def run(self, name, identity, client, method, args, kwargs):
        async with self.s._lock(name):
            row = self._row(name, identity)
            operation = self.s.registry.begin_operation(identity, row['generation'], method, self.clock())
            task = asyncio.create_task(self._operation(operation, client, method, args, kwargs))
            task.runtime_identity = identity
            self.tasks.add(task)
            task.add_done_callback(self._completed)
        # A lost HTTP waiter must not abort a kernel or clear its durable execution protection.
        return await asyncio.shield(task)

    def _completed(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve errors even when the caller disconnected.

    async def _operation(self, operation, client, method, args, kwargs):
        try:
            result = await getattr(client, method)(*args, **kwargs)
        except GatewayError as exc:
            # A rejected request has no runtime side effects. Transport/timeout failures
            # can leave remote work uncertain and intentionally keep the journal marker.
            if exc.status_code in {400, 404, 409, 422}:
                self.s.registry.finish_operation(operation, self.clock(), meaningful=False)
            raise
        else:
            self.s.registry.finish_operation(operation, self.clock())
            return result

    async def lease(self, name, kind, ttl):
        if kind not in {'interactive', 'agent'} or isinstance(ttl, bool) or not math.isfinite(ttl) or not 0 < ttl <= 300:
            raise ValueError('Lease requires interactive/agent kind and 0 < TTL <= 300 seconds')
        async with self.s._lock(name):
            identity = self.s.notebooks.get(name)
            row = self._row(name, identity)
            now = self.clock()
            activity = self.s.registry.activity(identity, now)
            if activity['stop_requested'] is not None:
                raise RuntimeErrorBase('Runtime stop has been requested')
            # Connecting/reconnecting is not meaningful activity. No heartbeat may
            # extend protection indefinitely after the last actual notebook operation.
            expires = min(now + ttl, activity['last_activity'] + 300)
            if expires <= now:
                raise GatewayError('Notebook activity required before opening a new session lease', 409)
            token = secrets.token_urlsafe(32)
            self.s.registry.add_lease(identity, row['generation'], kind, expires, hashlib.sha256(token.encode()).hexdigest(), now)
            return {'lease_token': token, 'kind': kind, 'expires_at': expires}

    async def release(self, name, token):
        async with self.s._lock(name):
            identity = self.s.notebooks.get(name)
            row = self._row(name, identity)
            self.s.registry.release_lease(identity, row['generation'], hashlib.sha256(token.encode()).hexdigest())

    async def stop(self, name):
        async with self.s._lock(name):
            identity = self.s.notebooks.get(name)
            if identity:
                self.s.registry.activity(identity, self.clock())
                self.s.registry.lifecycle_mark(identity, 'stop_requested', self.clock())
                # Only live local operations block explicit operator teardown. Unknown
                # crash leftovers can be explicitly destroyed via the existing safe path.
                if any(not t.done() and t.runtime_identity == identity for t in self.tasks) and self.s.registry.unfinished(identity):
                    raise GatewayError('Stop recorded; active work is protected. Retry after it finishes', 409)
            await self._destroy_locked(name)

    async def _destroy_locked(self, name):
        # Same notebook lock as operation admission; use the existing crash-consistent
        # ownership verification/destruction path without reacquiring that lock.
        s = self.s
        if name in s.reconciliation_notes and name not in s._cleanup_allowed:
            raise RuntimeErrorBase('Ownership is inconsistent; destruction is forbidden')
        if name not in s._allocations:
            if any(s.registry.key(r) == name for r in s.registry.records()):
                raise RuntimeErrorBase('Durable ownership has not been reconciled')
            return
        identities = tuple(s._allocations[name][1])
        await s._settle(s._destroy_admitted(name))
        for identity in identities:
            s._activity_clients.pop(identity, None)
        s.reconciliation_notes.pop(name, None)
        s.pool.schedule()

    async def sweep(self):
        if self.closed or not self.policy.enabled or not self.s.reconciled or self.s.recovery_blockers:
            return
        for row in self.s.registry.records():
            if row['kind'] != 'assigned' or row['phase'] != 'active':
                continue
            name, identity = row['notebook_id'], row['runtime_id']
            async with self.s._lock(name):
                current = self.s.registry.get(identity)
                if (current['phase'] != 'active' or self.s.notebooks.get(name) != identity
                        or name in self.s.reconciliation_notes):
                    continue
                now = self.clock()
                activity = self.s.registry.activity(identity, now)
                if activity['operations']:
                    continue
                stopping = activity['stop_requested'] is not None
                if not stopping and (activity['leases'] or now < activity['last_activity'] + self.policy.timeout):
                    continue
                candidate = activity['idle_candidate']
                if candidate is None:
                    self.s.registry.lifecycle_mark(identity, 'idle_candidate', now)
                    candidate = now
                if now < candidate + self.policy.grace:
                    continue
                # Never infer readiness or quiescence from just the journal. Read the
                # owned Gateway's actual kernel inventory before automatic destruction.
                client = self.s._clients.get(identity)
                if client is None:
                    continue
                try:
                    models = await client.list_kernels()
                    if (not isinstance(models, list) or any(not isinstance(m, dict) or
                            m.get('execution_state') != 'idle' or m.get('id') not in client._owned for m in models)
                            or len({m['id'] for m in models}) != len(models)
                            or {m['id'] for m in models} != client._owned):
                        continue
                    status = await self.s.inspect(identity)
                    if not status.ready:
                        continue
                    started = time.monotonic()
                    await self._destroy_locked(name)
                    self.measurements.append({'notebook_id': name,
                        'reason': 'stop' if stopping else 'idle',
                        'idle_detection_seconds': None if stopping else candidate - (activity['last_activity'] + self.policy.timeout),
                        'grace_elapsed_seconds': now - candidate,
                        'reservation_release_seconds': self.s.cleanup_measurements[-1]['reservation_release_seconds'],
                        'cleanup_and_release_seconds': time.monotonic() - started,
                        'retired_at': self.clock()})
                    self.last_error = None
                except Exception:
                    self.last_error = 'Idle cleanup reported an error; inspect durable state and reservations'

    def status(self):
        notebooks = []
        for row in self.s.registry.records():
            if row['kind'] != 'assigned':
                continue
            activity = self.s.registry.activity(row['runtime_id'], self.clock())
            live = sum(not t.done() and t.runtime_identity == row['runtime_id'] for t in self.tasks)
            notebooks.append(dict(notebook_id=row['notebook_id'], **activity,
                                  uncertain_operations=max(0, activity['operations'] - live)))
        return {'enabled': self.policy.enabled, 'timeout_seconds': self.policy.timeout,
                'grace_seconds': self.policy.grace, 'interval_seconds': self.policy.interval,
                'active_operations': sum(not t.done() for t in self.tasks), 'last_error': self.last_error,
                'notebooks': notebooks}

    async def close(self):
        self.closed = True
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.tasks:
            # Shutdown does not assert remote completion. On expiry, unresolved
            # operation rows survive, and startup requires explicit reconciliation.
            _, pending = await asyncio.wait(tuple(self.tasks), timeout=self.policy.grace)
            for client in self.s._clients.values():
                client._durable_detaching = True
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
