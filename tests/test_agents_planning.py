"""Notebook-scoped proposals commit only an exact approved roadmap version."""
import pytest

from agents.models import ModelReply, Scope
from agents.store import identity
from application.models import Membership, ProjectCreate, RoadmapUpdate
from test_agents_foundation import Script, body, events, harness, proposal, seed, wait
from test_agents_home import approval
from test_workspace_notebooks import physical


def setup(physical):
    notebook, doc, conversation = seed(physical, 'print("bounded notebook context")')
    journal = physical[3]
    project = journal.project_create(ProjectCreate(operation_id=identity(), title='Markov'))
    journal.membership(project['id'], Membership(operation_id=identity(), revision=1, notebook_id=notebook))
    return project['id'], notebook, doc, conversation['id']


async def ask(runtime, notebook, content='Plan a simple Markov chain', **refs):
    accepted = await runtime.create(body(content, mode='coordinate', scope=Scope(kind='notebook',resource_id=notebook), **refs))
    return accepted['id'], await wait(runtime,accepted['id'],'waiting_for_approval')


def records(journal, notebook):
    return journal.db.execute("SELECT id,revision FROM records WHERE kind='roadmap' AND notebook_id=?",(notebook,)).fetchall()


async def test_notebook_plan_persists_dependencies_and_canonical_conversation(physical):
    project, notebook, doc, conversation = setup(physical)
    cell = doc['cells'][0]['id']
    _, runtime = harness(physical)
    await runtime.start()
    try:
        run, pending = await ask(runtime, notebook, cell_id=cell, document_revision=2)
        offer = pending['approval']
        assert pending['conversation_id'] == conversation
        assert offer['target'] == {'scope':'notebook','notebook_id':notebook,'project_id':project,'revision':2}
        assert not records(physical[3],notebook)
        await runtime.decide(run, offer['id'], approval(offer))
        await wait(runtime,run,'completed')
        row = records(physical[3],notebook)[0]
        saved = physical[3].get(row['id'],'roadmap')
        tasks = saved['data']['tasks']
        assert [task['dependencies'] for task in tasks] == [[]] + [[task['id']] for task in tasks[:-1]]
        assert all(t['status']=='pending' and not t['accepted'] and not t['evidence'] for t in tasks)
        history = runtime.store.messages(conversation,0,10)['items']
        assert history[0]['data']['cell_id'] == cell and history[1]['data']['author_role']=='coordinator'
        revise, pending = await ask(runtime,notebook,'Revise the Markov roadmap for a beginner')
        offer = pending['approval']
        assert offer['target']['roadmap_id'] == row['id'] and offer['target']['roadmap_revision']==1
        await runtime.decide(revise,offer['id'],approval(offer))
        await wait(runtime,revise,'completed')
        assert records(physical[3],notebook)[0]['revision']==2
        assert len(records(physical[3],notebook))==1
    finally:
        await runtime.close()


async def test_notebook_and_roadmap_revision_conflicts_preserve_owner_updates(physical):
    _, notebook, doc, _ = setup(physical)
    _, runtime = harness(physical)
    await runtime.start()
    try:
        run,pending = await ask(runtime,notebook)
        offer = pending['approval']
        physical[2].edit_cell(notebook,2,'insert',text='owner edit')
        await runtime.decide(run,offer['id'],approval(offer))
        await wait(runtime,run,'completed')
        assert not records(physical[3],notebook)
        assert any('Notebook revision changed' in e['data'].get('error','') for e in events(runtime,run) if e['kind']=='tool_failed')

        run,pending = await ask(runtime,notebook)
        offer = pending['approval']
        await runtime.decide(run,offer['id'],approval(offer))
        await wait(runtime,run,'completed')
        row = records(physical[3],notebook)[0]
        saved = physical[3].get(row['id'],'roadmap')['data']
        run,pending = await ask(runtime,notebook)
        offer = pending['approval']
        human = RoadmapUpdate(operation_id=identity(),revision=1,objective='Owner revised plan',
                              tasks=[{k:v for k,v in task.items() if k not in {'accepted_by','progress','display'}} for task in saved['tasks']])
        updated = physical[3].roadmap_update(row['id'],human)
        await runtime.decide(run,offer['id'],approval(offer))
        await wait(runtime,run,'completed')
        assert physical[3].get(row['id'],'roadmap') == updated
        assert any('Roadmap revision changed' in e['data'].get('error','') for e in events(runtime,run) if e['kind']=='tool_failed')
    finally:
        await runtime.close()


async def test_plan_proposal_cannot_target_another_notebook_and_receives_real_cells(physical):
    project, notebook, doc, _ = setup(physical)
    notebooks = physical[2]
    receipt, _ = notebooks.begin(notebook,doc['cells'][0]['id'],2)
    notebooks.finish(notebook,receipt['execution_id'],{'status':'ok','execution_count':1,
        'outputs':[{'output_type':'stream','name':'stdout','text':'verified output\n'}]})
    other, _, _ = seed(physical,'Never expose this separate notebook')
    physical[3].membership(project,Membership(operation_id=identity(),revision=2,notebook_id=other))
    args = {'notebook_id':other,'revision':2,'objective':'Cross-scope change',
            'tasks':[{'id':'foreign','title':'Foreign','acceptance_criteria':['Review']}]}
    provider = Script(proposal('save_notebook_plan',args),ModelReply(content='Should never run.'))
    _, runtime = harness(physical,provider)
    await runtime.start()
    try:
        accepted = await runtime.create(body('Plan work',mode='coordinate',scope=Scope(kind='notebook',resource_id=notebook)))
        result = await wait(runtime,accepted['id'],'failed')
        assert 'outside this run grant' in result['error']
        context = str(provider.seen[0][0])
        assert 'bounded notebook context' in context
        assert 'verified output' in context
        assert 'Never expose this separate notebook' not in context
        assert not records(physical[3],other)
    finally:
        await runtime.close()
