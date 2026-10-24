"""One clean, fully reserved Gateway. No recycling, prediction or kernel prelaunch."""
import asyncio
import time
import uuid
from dataclasses import asdict

from kernel.gateway_client import GatewayClient
from runtime.admission import AdmissionRejected
from runtime.contracts import RuntimeErrorBase, RuntimeSpec
from runtime.recovery import RecoveringRuntimeSupervisor


class WarmPool:
    def __init__(self, supervisor, *, target=1, profile=None):
        if isinstance(target, bool) or not isinstance(target, int) or target not in (0, 1):
            raise ValueError('Warm target must be 0 or 1')
        self.supervisor = supervisor
        if target and supervisor.providers['containerd'].config.handler != 'runc':
            raise ValueError('Clean prewarming is validated only for runc; use target 0 for experimental handlers')
        self.target = target
        self.profile = profile or supervisor.admission.profiles.default
        supervisor.admission.profiles.resolve(RuntimeSpec('profile-check', resource_profile=self.profile))
        self.lock = asyncio.Lock()
        self.worker = None
        self.closed = False
        self.last_error = None
        self.measurements = []

    def rows(self):
        return [r for r in self.supervisor.registry.records() if r['kind'] == 'warm']

    def matches_template(self, row):
        s = self.supervisor
        config = s.providers['containerd'].config
        resources = s.admission.profiles.profiles[self.profile]
        return (row['profile_id'] == self.profile and s.registry.resources(row) == resources
                and row['image'] == config.image and row['handler'] == config.handler)

    def schedule(self):
        s = self.supervisor
        if self.closed or s._closing or not s.reconciled or s.recovery_blockers:
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._refill())

    async def wait(self):
        if self.worker:
            await asyncio.shield(self.worker)

    async def stop(self):
        self.closed = True
        if self.worker and not self.worker.done():
            self.worker.cancel()
        if self.worker:
            await asyncio.gather(self.worker, return_exceptions=True)

    async def _discard(self, row):
        s = self.supervisor
        key = s.registry.key(row)
        async with self.lock:
            if s.registry.get(row['runtime_id'])['kind'] != 'warm':
                return  # A cancellation must never destroy a concurrently assigned runtime.
            s.registry.transition(row['runtime_id'], 'deleting')
        try:
            await s._settle(s._destroy_admitted(key))
        except BaseException:
            if s.registry.get(row['runtime_id'])['phase'] != 'destroyed':
                s._quarantine(row, 'Warm cleanup could not be confirmed', global_block=True)
            raise

    async def _refill(self):
        s = self.supervisor
        try:
            for row in self.rows():
                if row['phase'] in {'reconciliation', 'deleting'} or not self.matches_template(row):
                    await self._discard(row)
            if self.target == 0:
                for row in self.rows():
                    if row['phase'] == 'ready':
                        await self._discard(row)
                return
            if s.foreground or self.rows():
                return  # Any uncertain warm row occupies the single slot too.
            identity = 'europa-' + uuid.uuid4().hex
            key = ('warm', identity)
            spec = RuntimeSpec(identity, provider='containerd', resource_profile=self.profile)
            profile, resources = await s.admission.reserve(key, spec,
                eligible=lambda: not s.foreground and not self.closed and not s.recovery_blockers)
            try:
                s.registry.create(None, identity, profile, resources, s.providers['containerd'].config, warm=True)
            except BaseException:
                await s.admission.release(key)
                raise
            provider = s.providers['containerd']
            s._allocations[key] = (provider, {identity})
            s._cleanup_allowed.add(key)
            s._lock(key)
            started = time.monotonic()
            try:
                handle = await provider.provision(RuntimeSpec(identity, provider='containerd',
                    resources=resources, allocation_id=identity))
                if handle.runtime_id != identity:
                    raise RuntimeErrorBase('Warm provider changed its durable allocation identity')
                client = GatewayClient(handle.endpoint.url, handle.endpoint.token)
                s._clients[identity] = client
                await client.__aenter__()
                handle = await self._verify(s.registry.get(identity), client)
                s.handles[identity] = handle
                s.notebooks[key] = identity
                s._generations[identity] = handle.generation
                async with self.lock:
                    s.registry.transition(identity, 'ready')
                    await s.admission.warm_ready(key)
                self.measurements.append({'replenishment_seconds': time.monotonic() - started})
                self.last_error = None
            except BaseException:
                await self._discard(s.registry.get(identity))
                raise
        except asyncio.CancelledError:
            raise
        except AdmissionRejected as exc:
            self.last_error = str(exc)
        except Exception:
            self.last_error = 'Warm provisioning failed; inspect recovery status before retrying'

    async def _verify(self, row, client):
        provider = self.supervisor.providers['containerd']
        handle = await provider.recover(row)
        await provider.verify_clean_workspace(row)
        await client.health()
        if await client.list_kernels() != []:
            raise RuntimeErrorBase('Warm Gateway contains a kernel; it cannot be assigned')
        return handle

    async def claim(self, spec, *, bootstrap=True):
        s = self.supervisor
        if not self.target or spec.environment or spec.working_directory is not None:
            return None
        try:
            profile, resources = s.admission.profiles.resolve(spec)
        except AdmissionRejected:
            return None  # Central admission preserves its normal validation/rejection accounting.
        provider = s.providers['containerd']
        if spec.provider != 'containerd':
            return None
        started = time.monotonic()
        bad = None
        async with self.lock:
            eligible = [row for row in self.rows() if row['phase'] == 'ready'
                and row['profile_id'] == profile and s.registry.resources(row) == resources
                and row['image'] == provider.config.image and row['handler'] == provider.config.handler]
            if not eligible:
                return None
            row = eligible[0]
            identity, name, key = row['runtime_id'], spec.notebook_id, s.registry.key(row)
            client = s._clients[identity]
            try:
                await self._verify(row, client)
            except Exception:
                s.registry.transition(identity, 'reconciliation', 'Warm cleanliness/readiness verification failed')
                bad = row
            if bad is None:
                await s.admission.claim(key, name, lambda: s.registry.claim(identity, name))
                s._allocations[name] = s._allocations.pop(key)
                s._cleanup_allowed.add(name)
                s.notebooks.pop(key, None)
                s.notebooks[name] = identity
                row = s.registry.get(identity)
                handle = provider.restore_record(row, client_endpoint(s, identity))
                s.handles[identity] = handle
                s._generations[identity] = handle.generation
                self.measurements.append({'warm_claim_seconds': time.monotonic() - started})
        if bad:
            await self._discard(bad)
            return None
        try:
            if bootstrap:
                started = time.monotonic()
                model = await client.create_kernel()
                self.measurements.append({'warm_kernel_start_seconds': time.monotonic() - started})
                s.registry.set_initial_kernel(identity, model['id'])
                client.offer_initial_kernel(model, lambda: s.registry.consume_initial_kernel(identity, model['id']))
            s.registry.transition(identity, 'active')
            await s.admission.activate(name)
            return handle
        except BaseException:
            await s._settle(s._destroy_admitted(name))
            raise

    def status(self):
        rows = self.rows()
        s = self.supervisor
        verified = not self.closed and s.reconciled and not s.recovery_blockers
        return {'enabled': bool(self.target), 'target': self.target, 'profile_id': self.profile,
                'ready': sum(verified and row['phase'] == 'ready' and self.matches_template(row)
                             and row['runtime_id'] in s._clients for row in rows),
                'unassigned_reservations': len(rows), 'worker_running': bool(self.worker and not self.worker.done()),
                'last_error': self.last_error,
                'runtimes': [{'runtime_id': row['runtime_id'], 'phase': row['phase'],
                              'resources': asdict(self.supervisor.registry.resources(row))} for row in rows]}


def client_endpoint(supervisor, identity):
    return supervisor.handles[identity].endpoint


class PrewarmingRuntimeSupervisor(RecoveringRuntimeSupervisor):
    def __init__(self, provider, admission, registry, *, target=1, profile=None):
        super().__init__(provider, admission, registry)
        self.foreground = 0
        self.startup_gateway_only = False
        self.pool = WarmPool(self, target=target, profile=profile)

    async def reconcile(self):
        await super().reconcile()
        if not self.recovery_blockers:
            for row in self.pool.rows():
                if not self.pool.matches_template(row):
                    try:
                        await self.pool._discard(row)
                    except Exception:
                        return  # Cleanup failure already retains ownership and sets reconciliation blockers.
        if self.pool.target == 0 and not self.recovery_blockers:
            self.pool.schedule()
            await self.pool.wait()  # Drain verified unassigned reservations before cold startup admission.

    async def _try_warm(self, spec):
        return await self.pool.claim(spec, bootstrap=not self.startup_gateway_only)

    async def ensure(self, spec):
        existing = spec.notebook_id in self.notebooks
        self.foreground += 1
        try:
            return await super().ensure(spec)
        finally:
            self.foreground -= 1
            if not existing:
                self.pool.schedule()

    async def destroy_notebook(self, notebook_id):
        await super().destroy_notebook(notebook_id)
        self.pool.schedule()

    async def admission_status(self):
        return {**await super().admission_status(), 'warm_pool': self.pool.status()}

    async def close(self):
        self._closing = True
        try:
            await self._settle(self.pool.stop())
        finally:
            await super().close()
