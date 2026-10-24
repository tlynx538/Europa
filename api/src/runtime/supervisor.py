"""Single-process notebook ownership; no scheduling, prewarming, or eviction."""
import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from kernel.gateway_client import GatewayClient, GatewayError
from runtime.admission import AdmissionRejected
from runtime.contracts import (
    GatewayEndpoint, RuntimeErrorBase, RuntimeHandle, RuntimeProvider, RuntimeSpec,
    RuntimeState, RuntimeStatus,
)


class RuntimeSupervisor:
    def __init__(self, providers: dict[str, RuntimeProvider], *, default_provider="local",
                 default_resources=None, admission=None):
        self.providers = providers
        self.default_provider = default_provider
        self.default_resources = default_resources
        self.admission = admission
        self.registry = None
        self._allocations = {}
        self.handles: dict[str, RuntimeHandle] = {}
        self.notebooks: dict[str, str] = {}
        self._clients: dict[str, GatewayClient] = {}
        self._generations: dict[str, int] = {}
        self._unavailable: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._closing = False

    def _lock(self, notebook_id):
        return self._locks.setdefault(notebook_id, asyncio.Lock())

    async def attach(self, notebook_id: str, endpoint: GatewayEndpoint):
        """Compatibility: connect an externally owned Gateway without launching it."""
        async with self._lock(notebook_id):
            if self._closing or notebook_id in self.notebooks:
                raise RuntimeErrorBase("Notebook runtime already exists or supervisor is closing")
            handle = RuntimeHandle(uuid.uuid4().hex, "external", notebook_id, endpoint, 1,
                                   RuntimeState.READY, datetime.now(timezone.utc).isoformat())
            client = GatewayClient(endpoint.url, endpoint.token)
            await client.__aenter__()
            try:
                await client.health()
            except BaseException:
                await client.close()
                raise
            self.handles[handle.runtime_id] = handle
            self.notebooks[notebook_id] = handle.runtime_id
            self._clients[handle.runtime_id] = client
            self._generations[handle.runtime_id] = handle.generation
            return handle

    async def ensure(self, spec: RuntimeSpec):
        async with self._lock(spec.notebook_id):
            if self._closing:
                raise RuntimeErrorBase("Runtime supervisor is closing")
            existing = self.notebooks.get(spec.notebook_id)
            if existing:
                if spec.resource_profile is not None and self.handles[existing].provider != "containerd":
                    raise AdmissionRejected("Resource profiles require a managed containerd runtime")
                if self.admission and (spec.resource_profile is not None or spec.resources is not None):
                    reservation = await self.admission.reservation(spec.notebook_id)
                    if reservation and spec.resource_profile is not None and reservation["profile_id"] != spec.resource_profile:
                        raise AdmissionRejected("Notebook already exists with a different resource profile")
                    profile, resources = self.admission.profiles.resolve(spec)
                    if reservation and (reservation["profile_id"] != profile or
                                        reservation["resources"] != vars(resources)):
                        raise AdmissionRejected("Notebook already exists with a different resource profile")
                status = await self.inspect(existing)
                if not status.ready:
                    raise RuntimeErrorBase("Notebook runtime is not ready; destroy it before provisioning again")
                return status.handle
            try:
                provider = self.providers[spec.provider]
            except KeyError as exc:
                raise RuntimeErrorBase("Unknown runtime provider") from exc
            if self.registry and (not self.reconciled or self.recovery_blockers):
                raise AdmissionRejected("Runtime recovery requires operator reconciliation; new admission denied")
            warm = await self._try_warm(spec)
            if warm is not None:
                return warm
            if self.admission and spec.provider == "containerd":
                return await self._ensure_admitted(spec, provider)
            if spec.resource_profile is not None:
                raise AdmissionRejected("Resource-profile admission is available only for managed containerd notebooks")
            handle = await provider.provision(spec)
            endpoint = handle.endpoint
            # Publish ownership before any further awaited setup. Failed cleanup
            # must leave a discoverable runtime for shutdown retry.
            self.handles[handle.runtime_id] = handle
            self.notebooks[spec.notebook_id] = handle.runtime_id
            self._generations[handle.runtime_id] = handle.generation
            client = None
            try:
                client = GatewayClient(endpoint.url, endpoint.token)
                await client.__aenter__()
                self._clients[handle.runtime_id] = client
                await client.health()
            except BaseException:
                if client:
                    await client.close()
                await provider.destroy(handle.runtime_id)
                self._clients.pop(handle.runtime_id, None)
                self._generations.pop(handle.runtime_id, None)
                self.handles.pop(handle.runtime_id, None)
                self.notebooks.pop(spec.notebook_id, None)
                raise
            return handle

    async def _try_warm(self, spec):
        return None

    @staticmethod
    async def _settle(coroutine):
        """Finish ownership cleanup despite repeated cancellation of its caller."""
        task = asyncio.create_task(coroutine)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise RuntimeErrorBase("Ownership cleanup was interrupted; reservation retained")
                cancelled = True
            except BaseException:
                if cancelled:
                    raise asyncio.CancelledError
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _ensure_admitted(self, spec, provider):
        name = spec.notebook_id
        if name in self._allocations:
            raise AdmissionRejected("Notebook startup cleanup is pending; destroy it before retrying")
        profile, resources = await self.admission.reserve(name, spec)
        identity = "europa-" + uuid.uuid4().hex
        if self.registry:
            try:
                self.registry.create(name, identity, profile, resources, provider.config)
            except BaseException:
                await self.admission.release(name)
                raise
        # Record a known identity before the provider can have any side effect.
        self._allocations[name] = (provider, {identity})
        spec = replace(spec, resources=resources, resource_profile=None, allocation_id=identity)
        try:
            handle = await provider.provision(spec)
            self._allocations[name][1].add(handle.runtime_id)
            self.handles[handle.runtime_id] = handle
            self.notebooks[name] = handle.runtime_id
            self._generations[handle.runtime_id] = handle.generation
            if handle.runtime_id != identity:
                raise RuntimeErrorBase("Provider did not preserve the reserved allocation identity")
            status = await provider.inspect(handle.runtime_id)
            if not status.ready or not status.resource_limits_enforced:
                raise RuntimeErrorBase("Admitted resource limits could not be verified on the ready runtime")
            client = GatewayClient(handle.endpoint.url, handle.endpoint.token)
            self._clients[handle.runtime_id] = client
            await client.__aenter__()
            await client.health()
            if self.registry:
                self.registry.transition(identity, 'active')
            await self.admission.activate(name)
            return handle
        except BaseException as original:
            try:
                await self._settle(self._destroy_admitted(name))
            except BaseException:
                if isinstance(original, asyncio.CancelledError):
                    raise original
                raise
            raise

    async def _destroy_admitted(self, name):
        provider, identities = self._allocations[name]
        if self.registry:
            for identity in identities:
                self.registry.transition(identity, 'deleting')
        client_error = None
        for identity in identities:
            self._unavailable.add(identity)
            client = self._clients.get(identity)
            if client:
                try:
                    await client.close()
                except Exception as exc:
                    client_error = exc
        try:
            for identity in identities:
                await provider.destroy(identity)
                if self.registry:
                    inventory = await provider.backend.request('inventory', identity)
                    if inventory != {'containers': 0, 'tasks': 0, 'snapshot_present': False,
                                     'network_state_present': False}:
                        raise RuntimeErrorBase('Runtime destruction could not be confirmed')
                    self.registry.transition(identity, 'destroyed')
        except BaseException:
            await self.admission.retain(name, cleanup_failed=True)
            raise
        # Only successful provider destruction proves the allocation no longer exists.
        await self.admission.release(name)
        for identity in identities:
            self._clients.pop(identity, None)
            self._generations.pop(identity, None)
            self.handles.pop(identity, None)
            self._unavailable.discard(identity)
        self.notebooks.pop(name, None)
        self._allocations.pop(name)
        if client_error:
            raise client_error

    async def inspect(self, runtime_id):
        try:
            handle = self.handles[runtime_id]
        except KeyError as exc:
            raise RuntimeErrorBase("Unknown runtime") from exc
        if handle.provider == "external":
            try:
                await self._clients[runtime_id].health()
                return RuntimeStatus(handle, None, True)
            except GatewayError:
                return RuntimeStatus(handle, None, False, "External Gateway is not ready")
        status = await self.providers[handle.provider].inspect(runtime_id)
        self.handles[runtime_id] = status.handle
        if self.admission and handle.notebook_id in self._allocations and not status.ready:
            await self.admission.retain(handle.notebook_id)
        if runtime_id in self._unavailable or runtime_id not in self._clients:
            return RuntimeStatus(status.handle, status.process_alive, False,
                                 "Runtime client is unavailable; cleanup retry required")
        if status.handle.generation != self._generations[runtime_id]:
            return RuntimeStatus(status.handle, status.process_alive, False,
                                 "Runtime generation changed outside supervisor ownership")
        return status

    async def client_for(self, notebook_id="default", *, resource_profile=None):
        handle = await self.ensure(RuntimeSpec(notebook_id, provider=self.default_provider,
                                              resources=self.default_resources if resource_profile is None else None,
                                              resource_profile=resource_profile))
        return self._clients[handle.runtime_id]

    async def destroy_notebook(self, notebook_id):
        async with self._lock(notebook_id):
            if notebook_id in self._allocations:
                await self._settle(self._destroy_admitted(notebook_id))
                return
            runtime_id = self.notebooks.get(notebook_id)
            if runtime_id is None:
                return
            handle = self.handles[runtime_id]
            self._unavailable.add(runtime_id)
            try:
                client = self._clients.get(runtime_id)
                if client:
                    await client.close()
            finally:
                if handle.provider != "external":
                    await self.providers[handle.provider].destroy(runtime_id)
            self._clients.pop(runtime_id, None)
            self._unavailable.discard(runtime_id)
            del self._generations[runtime_id]
            del self.handles[runtime_id]
            del self.notebooks[notebook_id]

    async def status_for(self, notebook_id):
        runtime_id = self.notebooks.get(notebook_id)
        if runtime_id is None:
            raise RuntimeErrorBase("Notebook has no runtime")
        status = await self.inspect(runtime_id)
        # Authentication material is explicitly excluded from public status.
        handle = status.handle
        result = {"runtime_id": runtime_id, "provider": handle.provider,
                "notebook_id": notebook_id, "generation": handle.generation,
                "state": handle.state, "process_alive": status.process_alive,
                "ready": status.ready, "failure": status.failure,
                "created_at": handle.created_at, "started_at": handle.started_at,
                "resource_limits_enforced": status.resource_limits_enforced}
        if self.admission:
            result["admission"] = await self.admission.reservation(notebook_id)
        return result

    async def admission_status(self):
        if self.admission is None:
            return {"enabled": False, "reason": "Local/external runtimes have no enforced resource admission"}
        return await self.admission.metrics()

    async def close(self):
        self._closing = True
        failures = []
        # Includes notebook locks whose provisioning has not published a handle
        # yet. Waiting on those locks prevents shutdown from missing a late launch.
        for notebook_id in tuple(self._locks):
            try:
                await self.destroy_notebook(notebook_id)
            except Exception as exc:
                failures.append(exc)
        # Provisioning can fail before returning a handle to the notebook registry.
        # Providers retain those partial allocations and must retry them at shutdown.
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception as exc:
                failures.append(exc)
        # A provider close may have reclaimed an unpublished allocation whose
        # earlier destroy failed. Confirm destruction again before releasing it.
        for notebook_id in tuple(self._allocations):
            try:
                await self.destroy_notebook(notebook_id)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeErrorBase("One or more notebook runtimes failed cleanup") from failures[0]
