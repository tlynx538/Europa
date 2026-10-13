"""Single-process notebook ownership; no scheduling, prewarming, or eviction."""
import asyncio
import uuid
from datetime import datetime, timezone

from kernel.gateway_client import GatewayClient, GatewayError
from runtime.contracts import (
    GatewayEndpoint, RuntimeErrorBase, RuntimeHandle, RuntimeProvider, RuntimeSpec,
    RuntimeState, RuntimeStatus,
)


class RuntimeSupervisor:
    def __init__(self, providers: dict[str, RuntimeProvider], *, default_provider="local",
                 default_resources=None):
        self.providers = providers
        self.default_provider = default_provider
        self.default_resources = default_resources
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
                status = await self.inspect(existing)
                if not status.ready:
                    raise RuntimeErrorBase("Notebook runtime is not ready; destroy it before provisioning again")
                return status.handle
            try:
                provider = self.providers[spec.provider]
            except KeyError as exc:
                raise RuntimeErrorBase("Unknown runtime provider") from exc
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
        if runtime_id in self._unavailable or runtime_id not in self._clients:
            return RuntimeStatus(status.handle, status.process_alive, False,
                                 "Runtime client is unavailable; cleanup retry required")
        if status.handle.generation != self._generations[runtime_id]:
            return RuntimeStatus(status.handle, status.process_alive, False,
                                 "Runtime generation changed outside supervisor ownership")
        return status

    async def client_for(self, notebook_id="default"):
        handle = await self.ensure(RuntimeSpec(notebook_id, provider=self.default_provider,
                                              resources=self.default_resources))
        return self._clients[handle.runtime_id]

    async def destroy_notebook(self, notebook_id):
        async with self._lock(notebook_id):
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
        return {"runtime_id": runtime_id, "provider": handle.provider,
                "notebook_id": notebook_id, "generation": handle.generation,
                "state": handle.state, "process_alive": status.process_alive,
                "ready": status.ready, "failure": status.failure,
                "created_at": handle.created_at, "started_at": handle.started_at,
                "resource_limits_enforced": status.resource_limits_enforced}

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
        if failures:
            raise RuntimeErrorBase("One or more notebook runtimes failed cleanup") from failures[0]
