"""Reconcile one private durable registry with its explicit Europa namespace."""
from kernel.gateway_client import GatewayClient
from runtime.contracts import RuntimeErrorBase
from runtime.supervisor import RuntimeSupervisor


def validate_inventory(inventory):
    """Absence is authoritative only when every expected inventory is complete."""
    if not isinstance(inventory, dict) or set(inventory) != {'containers', 'snapshots', 'tasks', 'state_directories'}:
        raise ValueError('Incomplete inventory')
    for key, values in inventory.items():
        if not isinstance(values, list):
            raise ValueError('Invalid inventory collection')
        identities = []
        for value in values:
            if key in {'containers', 'snapshots'}:
                if not isinstance(value, dict) or not isinstance(value.get('labels'), dict):
                    raise ValueError('Invalid ownership inventory')
                if key == 'containers' and any(field not in value for field in ('image', 'handler', 'resources')):
                    raise ValueError('Missing container configuration')
                identity = value.get('id')
            else:
                identity = value
            if not isinstance(identity, str) or not identity or identity in identities:
                raise ValueError('Invalid or duplicate resource identity')
            identities.append(identity)


class RecoveringRuntimeSupervisor(RuntimeSupervisor):
    def __init__(self, provider, admission, registry):
        super().__init__({'containerd': provider}, default_provider='containerd', admission=admission)
        self.registry = registry
        provider.registry = registry
        provider.backend.set_session(registry.new_session())
        self.reconciled = False
        self.recovery_blockers = []
        self.reconciliation_notes = {}
        self._cleanup_allowed = set()

    async def reconcile(self):
        """Startup only, before publishing the API. No automatic attachment by ID alone."""
        if self.reconciled:
            raise RuntimeErrorBase('Reconciliation already completed; restart to retry a failed inventory')
        provider = self.providers['containerd']
        rows = self.registry.records()
        for row in rows:
            await self.admission.restore(self.registry.key(row), row['profile_id'], self.registry.resources(row))
        try:
            inventory = await provider.recovery_inventory(begin_recovery=True)
            validate_inventory(inventory)
            containers = {item['id']: item for item in inventory['containers']}
            snapshots = {item['id']: item for item in inventory['snapshots']}
            tasks, states = set(inventory['tasks']), set(inventory['state_directories'])
            known = {r['runtime_id'] for r in rows}
            unknown = (set(containers) | set(snapshots) | tasks | states) - known
            if unknown:
                self.recovery_blockers.append('Unrecorded resources exist in the dedicated Europa namespace')
        except Exception:
            self.recovery_blockers.append('Containerd inventory is unavailable or invalid; reservations retained')
            self.reconciled = True
            return
        for row in rows:
            identity, name = row['runtime_id'], self.registry.key(row)
            self._lock(name)
            if identity not in set(containers) | set(snapshots) | tasks | states:
                # A complete authoritative inventory proves there is nothing left to own.
                self.registry.transition(identity, 'destroyed', 'Confirmed absent during startup inventory')
                await self.admission.release(name)
                continue
            labels = {'europa.runtime_id': identity, 'europa.notebook_id': row['allocation_key'],
                      'europa.owner_id': self.registry.owner_id}
            objects = [o for o in [containers.get(identity), snapshots.get(identity)] if o]
            if not objects or any(any(obj['labels'].get(k) != v for k, v in labels.items()) for obj in objects):
                self._quarantine(row, 'Runtime ownership labels disagree with the durable registry', global_block=True)
                continue
            # Container identity/configuration must match before any adoption or deletion.
            container = containers.get(identity)
            if container and (container['image'] != row['image'] or container['handler'] != row['handler'] or
                              container['resources'] != self.registry.enforced_resources(row)):
                self._quarantine(row, 'Runtime configuration disagrees with the durable reservation', global_block=True)
                continue
            self._cleanup_allowed.add(name)
            self._allocations[name] = (provider, {identity})
            if row['kind'] == 'assigned' and row['phase'] != 'deleting' and self.registry.unfinished(identity):
                provider.restore_record(row)
                self._quarantine(row, 'Unfinished notebook operation survived control-plane restart; explicit review/cleanup required', global_block=True)
                continue
            if row['phase'] in {'deleting', 'reconciliation', 'claimed'} or not container:
                provider.restore_record(row)
                if row['phase'] == 'deleting' or row['kind'] == 'warm':
                    try:
                        await self.destroy_notebook(name)
                    except Exception:
                        self._quarantine(row, 'Interrupted destruction remains incomplete')
                else:
                    self._quarantine(row, 'Incomplete allocation requires explicit destruction')
                continue
            try:
                handle = await provider.recover(row)
                client = GatewayClient(handle.endpoint.url, handle.endpoint.token)
                self._clients[identity] = client
                await client.__aenter__()
                await client.health()
                if row['kind'] == 'warm':
                    await provider.verify_clean_workspace(row)
                    if await client.list_kernels() != []:
                        raise RuntimeErrorBase('Unassigned Gateway is contaminated by a kernel')
                    self.registry.transition(identity, 'ready')
                else:
                    await client.recover_kernels()
                    if row['initial_kernel_id']:
                        model = await client.inspect_kernel(row['initial_kernel_id'])
                        client.offer_initial_kernel(model, lambda identity=identity, kernel=row['initial_kernel_id']:
                            self.registry.consume_initial_kernel(identity, kernel))
                    self.registry.transition(identity, 'active')
                self.handles[identity] = handle
                self.notebooks[name] = identity
                self._generations[identity] = handle.generation
                if row['kind'] == 'warm':
                    await self.admission.warm_ready(name)
                else:
                    await self.admission.activate(name)
            except Exception:
                client = self._clients.pop(identity, None)
                if client:
                    await client.disconnect()
                provider.restore_record(row)
                if row['kind'] == 'warm':
                    try:
                        await self._settle(self._destroy_admitted(name))
                        continue
                    except Exception:
                        pass
                self._quarantine(row, 'Surviving runtime identity, limits or Gateway readiness could not be verified',
                                 global_block=True)
        self.reconciled = True

    def _quarantine(self, row, reason, *, global_block=False):
        self.registry.transition(row['runtime_id'], 'reconciliation', reason)
        self.reconciliation_notes[self.registry.key(row)] = reason
        if global_block:
            self.recovery_blockers.append(reason)

    async def ensure(self, spec):
        if spec.notebook_id in self.reconciliation_notes:
            raise RuntimeErrorBase('Notebook requires operator reconciliation or explicit verified destruction')
        return await super().ensure(spec)

    async def inspect(self, runtime_id):
        status = await super().inspect(runtime_id)
        if not status.ready:
            name = self.registry.key(self.registry.get(runtime_id))
            await self.admission.retain(name)
            self.registry.transition(runtime_id, 'reconciliation', 'Runtime failed a live readiness check')
            self.reconciliation_notes[name] = 'Runtime failed a live readiness check; explicit destruction required'
            self._cleanup_allowed.add(name)
        return status

    async def destroy_notebook(self, notebook_id):
        if notebook_id in self.reconciliation_notes and notebook_id not in self._cleanup_allowed:
            raise RuntimeErrorBase('Ownership is inconsistent; automatic destruction is forbidden')
        if notebook_id not in self._allocations and any(self.registry.key(row) == notebook_id for row in self.registry.records()):
            raise RuntimeErrorBase('Durable ownership has not been reconciled; destruction cannot be confirmed')
        await super().destroy_notebook(notebook_id)
        self.reconciliation_notes.pop(notebook_id, None)

    async def recovery_status(self):
        return {'reconciled': self.reconciled, 'new_admissions_blocked': bool(self.recovery_blockers) or not self.reconciled,
                'blockers': list(self.recovery_blockers),
                'notebooks': {(f'warm/{k[1]}' if isinstance(k, tuple) else k): v for k, v in self.reconciliation_notes.items()},
                'records': [{k: row[k] for k in ('notebook_id', 'kind', 'runtime_id', 'provider', 'generation', 'phase', 'reason')}
                            for row in self.registry.records()]}

    async def admission_status(self):
        return {**await super().admission_status(), 'recovery': await self.recovery_status()}

    async def close(self):
        # A control-plane shutdown relinquishes clients, not durable runtime ownership.
        self._closing = True
        failures = []
        for name in tuple(self._locks):
            async with self._lock(name):
                identity = self.notebooks.get(name)
                client = self._clients.get(identity)
                if client:
                    try:
                        await client.disconnect()
                    except Exception as exc:
                        failures.append(exc)
        if failures:
            raise RuntimeErrorBase('Gateway client shutdown failed; durable ownership retained') from failures[0]
