"""Coding authorization, revision, real Gateway evidence, and recovery."""
import asyncio
import json

import pytest

from agents.coding import check_python
from agents.models import Assignment, Budgets, Control, ModelReply, Scope
from agents.providers import FakeProvider
from agents.store import AgentFault, identity
from notebooks.service import NotebookService
from runtime.local import LocalRuntimeProvider
from runtime.supervisor import RuntimeSupervisor
from test_agents_foundation import body, events, harness, proposal, wait
from test_agents_home import approval
from test_agents_planning import ask, records, setup
from test_workspace_notebooks import call, physical


async def accept(runtime, run, pending):
    offer = pending['approval']
    await runtime.decide(run,offer['id'],approval(offer))
    return offer


async def assignment(physical, runtime, notebook, *, provider_content='Assign this approved task'):
    run,pending = await ask(runtime,notebook)
    await accept(runtime,run,pending)
    await wait(runtime,run,'completed')
    row = records(physical[3],notebook)[0]
    roadmap = physical[3].get(row['id'],'roadmap')
    response = await call(physical[0],'get','roadmaps/'+roadmap['id'])
    assert response.status_code==200 and response.json()['data']['agent_approved'] is True
    selected = Assignment(roadmap_id=row['id'],roadmap_revision=row['revision'],task_id=roadmap['data']['tasks'][0]['id'])
    return body(provider_content,mode='coordinate',scope=Scope(kind='notebook',resource_id=notebook),
                assignment=selected,budgets=Budgets(requests=10))


async def delegate(runtime, request):
    parent = (await runtime.create(request))['id']
    pending = await wait(runtime,parent,'waiting_for_approval')
    await accept(runtime,parent,pending)
    await wait(runtime,parent,'completed')
    child = next(e['data']['child_id'] for e in events(runtime,parent) if e['kind']=='coding_delegated')
    return parent,child


@pytest.mark.parametrize('code', [
    'import os\nprint(os.listdir())','open("/etc/passwd")','print((1).__class__)',
    'exec("print(1)")','print(previous_notebook_global)','print = 0',
])
def test_calculation_scope_rejects_host_and_existing_global_access(code):
    with pytest.raises(AgentFault): check_python(code)


async def test_stale_approved_cell_edit_and_cancelled_execution_leave_no_receipt(physical):
    _, notebook, _, _ = setup(physical)
    _, runtime = harness(physical)
    await runtime.start()
    try:
        request = await assignment(physical,runtime,notebook)
        _,child = await delegate(runtime,request)
        review = await wait(runtime,child,'waiting_for_approval')
        physical[2].edit_cell(notebook,2,'insert',text='owner_change = 9')
        await accept(runtime,child,review)
        await wait(runtime,child,'completed')
        assert len(physical[2].open(notebook)['cells'])==2
        assert 'revision changed' in next(e['data']['error'] for e in events(runtime,child) if e['kind']=='tool_failed')
        _,child = await delegate(runtime,request.model_copy(update={'operation_id':identity()}))
        review = await wait(runtime,child,'waiting_for_approval')
        await accept(runtime,child,review)
        review = await wait(runtime,child,'waiting_for_approval')
        assert review['approval']['tool']=='execute_approved_cell'
        await runtime.control(child,Control(operation_id=identity()),'cancel')
        assert (await wait(runtime,child,'cancelled'))['approval'] is None
        assert physical[2].executions(notebook)==[]
    finally:
        await runtime.close()


class ErrorThenRepair(FakeProvider):
    async def complete(self,messages,tools,output_limit):
        names = {tool['function']['name'] for tool in tools}
        if 'apply_cell_edit' not in names: return await super().complete(messages,tools,output_limit)
        prefix = 'Authorized coding assignment: '
        context = json.loads(next(m['content'][len(prefix):] for m in messages
                                   if isinstance(m['content'],str) and m['content'].startswith(prefix)))
        notebook, revision = context['notebook_id'],context['revision']
        completed = [json.loads(m['content']) for m in messages if m['role']=='tool']
        if not completed:
            return proposal('apply_cell_edit',{'notebook_id':notebook,'revision':revision,'action':'insert',
                'source':'print(1 / 0)','explanation':'Fixture error'},'edit-1')
        last = completed[-1]['result']['data']
        if 'execution_id' not in last:
            return proposal('execute_approved_cell',{'notebook_id':notebook,**{k:last[k] for k in ('revision','cell_id','source_hash')}},
                            'execute-'+str(revision))
        assert last['status']=='error' and any('ZeroDivisionError' in output['text'] for output in last['outputs'])
        return proposal('apply_cell_edit',{'notebook_id':notebook,'revision':revision,'action':'replace','cell_id':last['cell_id'],
            'source':'print(2 / 0)','explanation':'Fixture repair still fails'},'repair-'+str(revision))


@pytest.mark.integration
async def test_real_error_receipts_replacement_and_bounded_repair(physical,tmp_path):
    app,_,store,_ = physical
    _,notebook,_,_ = setup(physical)
    provider = LocalRuntimeProvider(root=tmp_path)
    supervisor = RuntimeSupervisor({'local':provider})
    service = NotebookService(store,supervisor)
    app.state.notebook_service = service
    _,runtime = harness(physical,ErrorThenRepair())
    await runtime.start()
    try:
        request = await assignment(physical,runtime,notebook)
        parent,child = await delegate(runtime,request)
        for tool in ('apply_cell_edit','execute_approved_cell','apply_cell_edit','execute_approved_cell'):
            pending = await wait(runtime,child,'waiting_for_approval','failed','needs_reconciliation')
            assert pending['state']=='waiting_for_approval', pending['error']
            assert pending['approval']['tool']==tool
            await accept(runtime,child,pending)
        result = await wait(runtime,child,'failed')
        assert result['error']=='Coding repair budget exhausted'
        receipts = runtime.coding.executions(child)['items']
        assert len(receipts)==2
        assert all(r['status']=='error' and r['state']=='applied' for r in receipts)
        assert receipts[0]['cell_id']==receipts[1]['cell_id']
        assert [e['data']['execution_id'] for e in events(runtime,parent) if e['kind']=='coding_receipt_observed']==[r['execution_id'] for r in receipts]
        assert len(store.open(notebook)['cells'])==2
    finally:
        await runtime.close()
        await service.close()
        await supervisor.close()


@pytest.mark.integration
@pytest.mark.parametrize('scenario',['cancel','lost_ack','unknown'])
async def test_accepted_execution_settles_independently_or_is_never_replayed(physical,tmp_path,monkeypatch,scenario):
    app,_,store,_ = physical
    _,notebook,_,_ = setup(physical)
    supervisor = RuntimeSupervisor({'local':LocalRuntimeProvider(root=tmp_path)})
    service = NotebookService(store,supervisor)
    app.state.notebook_service = service
    _,runtime = harness(physical)
    release, entered = asyncio.Event(), asyncio.Event()
    invocations = []
    client_for = supervisor.client_for

    async def wrapped_client(*args,**kwargs):
        client = await client_for(*args,**kwargs)
        execute = client.execute
        async def gated(*args,**kwargs):
            invocations.append(True)
            entered.set()
            await release.wait()
            result = await execute(*args,**kwargs)
            if scenario=='unknown': raise ConnectionError('Gateway response lost after execution')
            return result
        monkeypatch.setattr(client,'execute',gated)
        return client

    monkeypatch.setattr(supervisor,'client_for',wrapped_client)
    if scenario=='lost_ack':
        submit = service.submit
        async def lost_ack(*args,**kwargs):
            await submit(*args,**kwargs)
            raise ConnectionError('Agent did not receive the prepared acknowledgement')
        monkeypatch.setattr(service,'submit',lost_ack)
    await runtime.start()
    try:
        request = await assignment(physical,runtime,notebook)
        _,child = await delegate(runtime,request)
        await accept(runtime,child,await wait(runtime,child,'waiting_for_approval'))
        offer = await accept(runtime,child,await wait(runtime,child,'waiting_for_approval'))
        await asyncio.wait_for(entered.wait(),6)
        assert runtime.coding.executions(child)['items'][0]['state']=='running'
        if scenario=='cancel':
            await runtime.control(child,Control(operation_id=identity()),'cancel')
            await wait(runtime,child,'cancelling')
        release.set()
        await asyncio.gather(*list(service.tasks.values()))
        await wait(runtime,child,'cancelled' if scenario=='cancel' else 'needs_reconciliation')
        expected = 'uncertain' if scenario=='unknown' else 'applied'
        assert runtime.coding.executions(child)['items'][0]['state']==expected
        await runtime.close()
        _,reopened = harness(physical)
        await reopened.start()
        try:
            await reopened.decide(child,offer['id'],approval(offer))
            assert reopened.coding.executions(child)['items'][0]['state']==expected
            if scenario=='lost_ack':
                assert reopened.coding.reconcile(child,Control(operation_id=identity()))['state']=='failed'
            if scenario=='unknown':
                with pytest.raises(AgentFault,match='no verified terminal result'):
                    reopened.coding.reconcile(child,Control(operation_id=identity()))
            assert invocations==[True] and len(store.executions(notebook))==1
            assert supervisor.notebooks.get(notebook)
        finally:
            await reopened.close()
    finally:
        release.set()
        await runtime.close()
        await service.close()
        await supervisor.close()


async def test_coordinator_cannot_use_child_edit_capability(physical,monkeypatch):
    _,notebook,_,_ = setup(physical)
    _,runtime = harness(physical)
    await runtime.start()
    try:
        request = await assignment(physical,runtime,notebook)
        async def malicious(*_):
            return proposal('apply_cell_edit',{'notebook_id':notebook,'revision':2,'action':'insert',
                'source':'print(1)','explanation':'Coordinator cannot do this'})
        monkeypatch.setattr(runtime.provider,'complete',malicious)
        run = (await runtime.create(request))['id']
        result = await wait(runtime,run,'failed')
        assert 'not granted to this agent role' in result['error']
        assert len(physical[2].open(notebook)['cells'])==1
        assert physical[2].executions(notebook)==[]
    finally:
        await runtime.close()
