"""Bounded dashboard projections. No provisioning, provider inspection or journal writes."""
import asyncio
from datetime import datetime
import math
import os
import time

from fastapi import APIRouter, Query, Request

from notebooks.store import ID, identifier

router = APIRouter(prefix='/dashboard')
PAGE = 20
FRESH_SECONDS = 15


def document_info(store, app, notebook):
    result = {'id': notebook, 'title': 'Unavailable notebook', 'location': 'Unavailable', 'available': False}
    try:
        if hasattr(store, 'dashboard_info'):
            document, location = store.dashboard_info(notebook)
        else:
            document, location = store.open(notebook), None
        store._validate(document)
        meta = store._meta(document)
        if meta.get('notebook_id') != notebook or type(meta.get('revision')) is not int or meta['revision'] < 1:
            raise ValueError('Invalid managed identity')
        store._title(meta.get('title'))
        result.update(title=meta['title'], available=True)
        if location:
            result['location'] = 'Home' + (' / ' + location['path'].rsplit('/', 1)[0].replace('/', ' / ') if '/' in location['path'] else '')
        else:
            row = app.db.execute("SELECT records.data FROM memberships JOIN records ON records.id=memberships.project_id WHERE memberships.notebook_id=? AND associated=1", (notebook,)).fetchone()
            import json
            result['location'] = 'Catalog / ' + json.loads(row['data'])['title'] if row else 'Private catalog'
    except Exception:
        result.update(title='Unavailable notebook', location='Unavailable', available=False)
        # Never publish stale paths or project claims when verification fails.
        pass
    return result


def snapshot(app, store, cursor=None, limit=PAGE, recent=False):
    with store.locked(), app.mutex:
        if recent:
            # Do not call ApplicationStore.transaction: it reconciles/writes the catalog.
            rows = app.db.execute('SELECT notebook_id,opened_at FROM recent_opens ORDER BY opened_at DESC,notebook_id LIMIT ?', (limit,)).fetchall()
            return {'items': [dict(document_info(store, app, row['notebook_id']), opened_at=row['opened_at']) for row in rows]}
        ids = [row[0] for row in app.db.execute('SELECT id FROM catalog WHERE id>? ORDER BY id LIMIT ?', (cursor or '', limit + 1))]
        selected = ids[:limit]
        version = os.fstat(store.fd).st_mtime_ns
        info = {nid: dict(document_info(store, app, nid), receipts=[], history_limited=False) for nid in selected}
        # One directory walk per page. Bound decoded receipts and aggregate byte cost.
        budget = 32 * 1024**2
        with os.scandir(store.fd) as entries:
            for entry in entries:
                nid = entry.name[:32]
                if nid not in info or not entry.name.startswith(nid + '.') or not entry.name.endswith('.execution.json') or not ID.fullmatch(entry.name[33:-15]):
                    continue
                item = info[nid]
                size = entry.stat(follow_symlinks=False).st_size
                if len(item['receipts']) >= 100 or size > budget:
                    item['history_limited'] = True
                    continue
                budget -= size
                try:
                    receipt = store.execution(nid, entry.name[33:-15])
                    item['receipts'].append({key: receipt.get(key) for key in ('state', 'runtime_id', 'generation', 'kernel_id')})
                except Exception:
                    item['history_limited'] = True
        stable = version == os.fstat(store.fd).st_mtime_ns
        for item in info.values():
            item['evidence_version'] = version if stable else None
        return {'items': list(info.values()), 'next_cursor': selected[-1] if len(ids) > limit else None}


class DashboardObservation:
    def __init__(self, app):
        self.app = app
        self.flights = {}
        self.slots = asyncio.Semaphore(4)

    async def sessions(self, cursor=None, limit=PAGE):
        key = cursor, limit
        if key not in self.flights:
            if len(self.flights) >= 4:
                from fastapi import HTTPException
                raise HTTPException(503, 'Observation capacity reached; refresh later')
            task = asyncio.create_task(self._page(cursor, limit))
            self.flights[key] = task
            def done(completed):
                self.flights.pop(key, None)
                if not completed.cancelled():
                    completed.exception()
            task.add_done_callback(done)
        return await asyncio.shield(self.flights[key])

    async def close(self):
        tasks = list(self.flights.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _page(self, cursor, limit):
        state = self.app.state
        page = await asyncio.to_thread(snapshot, state.application_store, state.notebook_store, cursor, limit)
        async def observe(item):
            base = {key: item[key] for key in ('id', 'title', 'location', 'available')}
            base.update(state='Unknown', reason='Observation unavailable', observed_at=None, last_activity=None, expires_at=None)
            try:
                async with self.slots:
                    async with asyncio.timeout(1):
                        return await self._one(item, base)
            except Exception:
                return base
        tasks = [asyncio.create_task(observe(item)) for item in page['items']]
        if tasks:
            try:
                done, pending = await asyncio.wait(tasks, timeout=4)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            page['items'] = [result if isinstance(result, dict) else {**{k: item[k] for k in ('id', 'title', 'location', 'available')}, 'state': 'Unknown', 'reason': 'Observation deadline exceeded', 'observed_at': None, 'expires_at': None, 'last_activity': None} for item, result in zip(page['items'], results)]
        return page

    def _registry(self, notebook):
        s = self.app.state.supervisor
        if not s.registry:
            return []
        # Registry SQLite belongs to this event loop. All SELECTs; activity() writes on upgrade.
        return [dict(row) for row in s.registry.db.execute("SELECT runtime_id,generation,phase,(SELECT COUNT(*) FROM operations WHERE operations.runtime_id=allocations.runtime_id) AS operations FROM allocations WHERE notebook_id=? AND kind='assigned' AND phase!='destroyed'", (notebook,))]

    async def _one(self, item, result):
        s = self.app.state.supervisor
        notebook = item['id']
        identity = s.notebooks.get(notebook)
        handle = s.handles.get(identity)
        client = s._clients.get(identity)
        service = self.app.state.notebook_service
        task = service.tasks.get(notebook)
        active = task is not None and not task.done()
        def state(label, reason, activity=None):
            now = time.time()
            return dict(result, state=label, reason=reason, observed_at=now if label != 'Unknown' else None, expires_at=now + FRESH_SECONDS if label != 'Unknown' else None, last_activity=activity)
        if item['evidence_version'] is None or item['evidence_version'] != os.fstat(self.app.state.notebook_store.fd).st_mtime_ns:
            return state('Unknown', 'Notebook or receipt evidence changed during observation')
        if not item['available']:
            return state('Unknown', 'Notebook or verified location is unavailable')
        rows = self._registry(notebook)
        if (getattr(s, '_closing', False) or getattr(s, 'recovery_blockers', []) or notebook in getattr(s, 'reconciliation_notes', {}) or
                (s.registry and not getattr(s, 'reconciled', False))):
            return state('Needs attention', 'Runtime ownership requires reconciliation')
        if rows and (len(rows) != 1 or rows[0]['phase'] in {'deleting', 'reconciliation', 'claimed'}):
            return state('Needs attention', 'Runtime lifecycle is unresolved')
        if rows:
            lifecycle = getattr(s, 'lifecycle', None)
            live = sum(not t.done() and getattr(t, 'runtime_identity', None) == rows[0]['runtime_id'] for t in lifecycle.tasks) if lifecycle else 0
            if rows[0]['operations'] > live:
                return state('Needs attention', 'Durable runtime operations have an unresolved outcome')
        receipts = item['receipts']
        relevant = [r for r in receipts if not identity or not r['runtime_id'] or (r['runtime_id'] == identity and handle and r['generation'] == handle.generation)]
        if item['history_limited']:
            return state('Unknown', 'Receipt evidence exceeds the bounded observation budget')
        if any(r['state'] == 'uncertain' or r['state'] == 'completed' or (r['state'] in {'prepared', 'running'} and not active) for r in relevant):
            return state('Needs attention', 'An execution receipt has an unresolved outcome')
        if identity is None:
            if active:
                return state('Starting', 'An accepted execution is acquiring its runtime')
            lock = s._locks.get(notebook)
            if lock and lock.locked():
                return state('Unknown', 'A runtime lifecycle transition is in progress')
            if rows:
                return state('Needs attention', 'Durable runtime has no verified supervisor binding')
            if s.registry:
                return state('Stopped', 'Reconciled registry has no assigned live runtime')
            records = [record for provider in s.providers.values() for record in getattr(provider, '_runtimes', {}).values() if record.handle.notebook_id == notebook]
            if records:
                if all(record.handle.state in {'stopped', 'destroyed'} and record.process is None for record in records):
                    return state('Stopped', 'Local provider completed owned process teardown')
                return state('Unknown', 'A local allocation lacks a verified supervisor binding')
            if any(r['runtime_id'] for r in receipts):
                return state('Unknown', 'Local runtime history cannot establish absence after restart')
            return state('Stopped', 'No runtime has been assigned in this owner session or receipt history')
        if not handle or identity in s._unavailable or not client or getattr(client, '_closing', False) or s._generations.get(identity) != handle.generation:
            return state('Needs attention', 'Runtime binding or generation is unavailable')
        if rows and (rows[0]['runtime_id'] != identity or rows[0]['generation'] != handle.generation):
            return state('Needs attention', 'Durable and live runtime bindings disagree')
        if handle.state == 'starting' or (rows and rows[0]['phase'] == 'provisioning'):
            return state('Starting', 'Owned runtime provisioning is in progress')
        if handle.state != 'ready':
            return state('Needs attention', 'Owned runtime is not ready')
        owned = set(client._owned)
        models = await client.list_kernels()  # Authenticated GET; never client_for/inspect/provider cleanup.
        if (s.notebooks.get(notebook) != identity or s.handles.get(identity) != handle or s._clients.get(identity) is not client or
                s._generations.get(identity) != handle.generation or set(client._owned) != owned or identity in s._unavailable or getattr(client, '_closing', False)):
            return state('Unknown', 'Runtime binding changed during observation')
        if (self._registry(notebook) != rows or notebook in getattr(s, 'reconciliation_notes', {}) or getattr(s, 'recovery_blockers', []) or
                item['evidence_version'] != os.fstat(self.app.state.notebook_store.fd).st_mtime_ns):
            return state('Unknown', 'Lifecycle or receipt evidence changed during observation')
        task = service.tasks.get(notebook)
        active = task is not None and not task.done()
        if not active and any(r['state'] in {'prepared', 'running'} for r in relevant):
            return state('Needs attention', 'Execution ownership ended without a terminal receipt')
        if rows:
            lifecycle = getattr(s, 'lifecycle', None)
            live = sum(not t.done() and getattr(t, 'runtime_identity', None) == rows[0]['runtime_id'] for t in lifecycle.tasks) if lifecycle else 0
            if rows[0]['operations'] > live:
                return state('Needs attention', 'Durable runtime operation ownership ended without resolution')
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            return state('Unknown', 'Invalid Gateway inventory')
        ids = [m.get('id') for m in models]
        if len(ids) != len(set(ids)) or set(ids) != owned or any(m.get('name') != 'python3' for m in models):
            return state('Needs attention', 'Gateway kernel inventory does not match verified ownership')
        activity = None
        for model in models:
            try:
                value = datetime.fromisoformat(model['last_activity'].replace('Z', '+00:00'))
                if value.tzinfo is not None and math.isfinite(value.timestamp()):
                    activity = max(activity or 0, value.timestamp())
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        if any(m.get('execution_state') == 'busy' for m in models):
            return state('Busy', 'Verified Gateway kernel reports busy', activity)
        if active and (client._busy or any(r['state'] == 'running' and r['kernel_id'] in owned for r in relevant)):
            return state('Busy', 'Accepted execution is still active on its verified kernel', activity)
        if active:
            return state('Starting', 'Accepted execution is preparing its kernel', activity)
        if client._busy:
            return state('Unknown', 'A kernel operation is still settling')
        if not models:
            return state('Stopped', 'Verified Gateway inventory contains no kernels')
        if all(m.get('execution_state') == 'idle' for m in models):
            return state('Idle', 'All verified owned kernels report idle', activity)
        if any(m.get('execution_state') == 'starting' for m in models):
            return state('Starting', 'Verified Gateway kernel reports starting', activity)
        return state('Unknown', 'Gateway did not provide a recognized execution state', activity)


@router.get('/recent')
async def recent(request: Request, limit: int = Query(PAGE, ge=1, le=PAGE)):
    return await asyncio.to_thread(snapshot, request.app.state.application_store, request.app.state.notebook_store, limit=limit, recent=True)


@router.get('/sessions')
async def sessions(request: Request, cursor: str | None = None, limit: int = Query(PAGE, ge=1, le=PAGE)):
    if cursor is not None:
        identifier(cursor)
    return await request.app.state.dashboard_observation.sessions(cursor, limit)
