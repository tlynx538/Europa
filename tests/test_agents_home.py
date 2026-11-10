"""Workspace coordinator effects and approval-bound physical creation."""
import json

import pytest

from agents import home
from agents.models import Decision, ModelReply
from agents.store import AgentFault, identity
from test_agents_foundation import Script, body, events, harness, proposal, wait
from test_workspace_notebooks import call, physical


def approval(offer, decision='granted'):
    return Decision(operation_id=identity(), digest=offer['digest'], decision=decision)


async def pending(runtime, content='I have revenue data and want to forecast the next quarter.', **kwargs):
    run = await runtime.create(body(content, mode='coordinate', **kwargs))
    result = await wait(runtime, run['id'], 'waiting_for_approval')
    return run, result['approval']


async def test_home_creation_and_handoff_survive_reopen(physical):
    app, root, notebooks, journal = physical
    _, runtime = harness(physical)
    await runtime.start()
    try:
        run, offer = await pending(runtime)
        path = root / 'Revenue Forecasting' / 'exploratory-analysis.ipynb'
        assert offer['target']['notebook_path'] == str(path.relative_to(root))
        assert not path.parent.exists()
        with pytest.raises(AgentFault, match='digest'):
            await runtime.decide(run['id'], offer['id'], approval(offer).model_copy(update={'digest':'0'*64}))
        review = approval(offer)
        decision = await runtime.decide(run['id'], offer['id'], review)
        assert await runtime.decide(run['id'], offer['id'], review.model_copy(update={'operation_id':identity()})) == decision
        await wait(runtime, run['id'], 'completed')
        data = json.loads(path.read_text())['metadata']['europa']
        nid = data['notebook_id']
        project = app.state.workspace.project(str(path.parent.relative_to(root)))
        assert notebooks.location(nid)['project_id'] == project['id']
        assert not (root.parent / 'private' / f'{nid}.ipynb').exists()
        canonical = journal.ensure_notebook_conversation(nid)
        handoff = runtime.store.handoffs(run['conversation_id'], 10)['items'][0]
        assert handoff['notebook_id'] == nid
        assert handoff['destination_conversation_id'] == canonical['id']
        assert handoff['plan']['digest'] == review.digest
        assert runtime.store.handoffs(canonical['id'], 10)['items'] == [handoff]
        reopened = await call(app, 'post', 'workspace/notebooks/open', json={'path':handoff['path'],'operation_id':identity()})
        assert reopened.json()['notebook_id'] == nid
        assert [e['kind'] for e in events(runtime, run['id'])].count('tool_started') == 1
    finally:
        await runtime.close()


async def test_denied_home_approval_has_no_effect(physical):
    _, root, _, _ = physical
    _, runtime = harness(physical)
    await runtime.start()
    try:
        run, offer = await pending(runtime)
        await runtime.decide(run['id'], offer['id'], approval(offer, 'denied'))
        await wait(runtime, run['id'], 'cancelled')
        assert not list(root.iterdir())
        assert not runtime.store.handoffs(run['conversation_id'], 10)['items']
    finally:
        await runtime.close()


async def test_existing_notebook_collision_preserves_document(physical):
    app, root, _, _ = physical
    project = (await call(app, 'post', 'workspace/projects', json={'path':'Revenue','operation_id':identity()})).json()
    path = root / 'Revenue' / 'analysis.ipynb'
    created = await call(app, 'post', 'workspace/notebooks', json={'path':'Revenue/analysis.ipynb','operation_id':identity()})
    assert created.status_code == 201
    before = path.read_bytes()
    provider = Script(proposal('create_workspace_notebook', {
        'path':'Revenue','notebook_name':'analysis.ipynb','objective':'Analyze data','project_id':project['id'],
    }), ModelReply(content='Choose another filename.'))
    _, runtime = harness(physical, provider)
    await runtime.start()
    try:
        run, offer = await pending(runtime)
        await runtime.decide(run['id'], offer['id'], approval(offer))
        await wait(runtime, run['id'], 'completed')
        assert path.read_bytes() == before
        failure = next(e for e in events(runtime,run['id']) if e['kind']=='tool_failed')
        assert failure['data']['result']['notebook_created'] is False
        assert not runtime.store.handoffs(run['conversation_id'], 10)['items']
    finally:
        await runtime.close()


async def test_lost_acknowledgement_preserves_mapping_and_prevents_replay(physical, monkeypatch):
    _, root, notebooks, _ = physical
    associate = home.associate_notebook

    def lose_ack(*args, **kwargs):
        associate(*args, **kwargs)
        raise OSError('lost acknowledgement')

    monkeypatch.setattr(home, 'associate_notebook', lose_ack)
    _, runtime = harness(physical)
    await runtime.start()
    try:
        run, offer = await pending(runtime)
        review = approval(offer)
        await runtime.decide(run['id'], offer['id'], review)
        await wait(runtime, run['id'], 'needs_reconciliation')
        await runtime.decide(run['id'], offer['id'], review)
        path = root / 'Revenue Forecasting' / 'exploratory-analysis.ipynb'
        nid = json.loads(path.read_text())['metadata']['europa']['notebook_id']
        assert notebooks.location(nid)['path'] == str(path.relative_to(root))
        assert [e['kind'] for e in events(runtime,run['id'])].count('tool_started') == 1
        assert not runtime.store.handoffs(run['conversation_id'], 10)['items']
    finally:
        await runtime.close()
