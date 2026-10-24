"""Own accepted document executions independently of HTTP waiters and storage locks."""
import asyncio

from kernel.gateway_client import GatewayError
from notebooks.outputs import notebook_result
from notebooks.store import NotebookError
from runtime.supervisor import RuntimeSupervisor


class NotebookService:
    def __init__(self, store, supervisor):
        self.store, self.supervisor = store, supervisor
        self.tasks = {}
        self.closed = False

    @staticmethod
    def _retrieve(future):
        if not future.cancelled(): future.exception()

    async def submit(self, notebook, cell, revision, *, kernel_id=None, timeout=30):
        if self.closed: raise NotebookError('Notebook execution service is closing', 503)
        if notebook in self.tasks: raise NotebookError('Notebook already has an active document execution', 409)
        ready = asyncio.get_running_loop().create_future()
        ready.add_done_callback(self._retrieve)
        task = asyncio.create_task(self._run(ready, notebook, cell, revision, kernel_id, timeout))
        self.tasks[notebook] = task
        task.add_done_callback(lambda done: self._done(notebook, done))
        return await asyncio.shield(ready)

    def _done(self, notebook, task):
        self.tasks.pop(notebook, None)
        self._retrieve(task)

    async def _run(self, ready, notebook, cell, revision, kernel_id, timeout):
        receipt = None
        try:
            receipt, code = await asyncio.to_thread(self.store.begin, notebook, cell, revision)
            ready.set_result(receipt)
            client = await self.supervisor.client_for(notebook)
            identity = self.supervisor.notebooks[notebook]
            handle = self.supervisor.handles[identity]
            if kernel_id is None:
                # Only matching verified runtime identity/generation may reuse a saved binding.
                history = await asyncio.to_thread(self.store.executions, notebook)
                for previous in reversed(history):
                    if previous.get('runtime_id') == identity and previous.get('generation') == handle.generation and previous.get('kernel_id'):
                        candidate = previous['kernel_id']
                        try: await client.inspect_kernel(candidate)
                        except GatewayError as exc:
                            if exc.status_code != 404: raise
                        else: kernel_id = candidate; break
            if kernel_id is None and not getattr(client, '_initial_kernel', None):
                owned = getattr(client, '_owned', set())
                if len(owned) == 1:
                    kernel_id = next(iter(owned))
                elif len(owned) > 1:
                    raise NotebookError('Multiple owned kernels exist; specify kernel_id', 409)
            if kernel_id is None:
                kernel_id = (await client.create_kernel())['id']
            else:
                await client.inspect_kernel(kernel_id)  # Client rejects kernels owned by another runtime.
            await asyncio.to_thread(self.store.update_execution, notebook, receipt['execution_id'], state='running',
                runtime_id=identity, generation=handle.generation, kernel_id=kernel_id)
            result = await client.execute(kernel_id, code, timeout=timeout)
            await asyncio.to_thread(self.store.finish, notebook, receipt['execution_id'], notebook_result(result))
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            elif receipt is not None:
                # Transport failure, cancellation or crash is not proof of remote completion.
                # Persist only sanitized error type; never fabricate success or saved output.
                current = await asyncio.to_thread(self.store.execution, notebook, receipt['execution_id'])
                if current['state'] not in {'completed','applied','conflict'}:
                    await RuntimeSupervisor._settle(asyncio.to_thread(self.store.uncertain, notebook,
                        receipt['execution_id'], failure=type(exc).__name__))
            if isinstance(exc, asyncio.CancelledError): raise

    async def close(self, grace=30):
        self.closed = True
        if self.tasks:
            _, pending = await asyncio.wait(tuple(self.tasks.values()), timeout=grace)
            for task in pending: task.cancel()
            if pending: await asyncio.gather(*pending, return_exceptions=True)
