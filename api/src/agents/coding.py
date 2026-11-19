"""Supervised edits and execution through the existing notebook APIs."""
import ast
import asyncio
import re
import time
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from agents.home import UncertainEffect
from agents.models import Assignment, Model
from agents.store import AgentFault
from agents.tools import NotebookTarget, Tool, ToolResult, preview_cell
from application.store import encoded
from notebooks.store import NotebookError, source_hash

SAFE_FUNCTIONS = {'abs','all','any','bool','enumerate','float','int','len','list','max',
                  'min','print','range','round','sorted','str','sum','tuple','zip'}
SAFE_SYNTAX = (ast.Module, ast.Assign, ast.AugAssign, ast.Name, ast.Constant, ast.List,
               ast.Tuple, ast.Dict, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Expr,
               ast.Call, ast.If, ast.IfExp, ast.For, ast.Pass, ast.Subscript, ast.Slice,
               ast.ListComp, ast.comprehension, ast.Load, ast.Store, ast.operator,
               ast.unaryop, ast.boolop, ast.cmpop)


def check_python(code):
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise AgentFault('Proposed code must be valid Python',400) from exc
    if any(not isinstance(node, SAFE_SYNTAX) for node in ast.walk(tree)):
        raise AgentFault('Supervised execution supports local Python calculations without imports or attribute access',403)

    def target(node, names):
        if isinstance(node,ast.Tuple):
            for item in node.elts: target(item,names)
        elif isinstance(node,ast.Name) and not node.id.startswith('_') and node.id not in SAFE_FUNCTIONS:
            names.add(node.id)
        else: raise AgentFault('Assignment requires a local calculation variable',403)

    def expression(node, names):
        if isinstance(node,ast.Name) and node.id not in names:
            raise AgentFault('Proposed code references a name outside the calculation scope',403)
        if isinstance(node,ast.Call):
            if not isinstance(node.func,ast.Name) or node.func.id not in SAFE_FUNCTIONS:
                raise AgentFault('Proposed code calls a function outside the calculation scope',403)
            for arg in node.args: expression(arg,names)
        elif isinstance(node,ast.ListComp):
            local = names.copy()
            for generator in node.generators:
                expression(generator.iter,local)
                target(generator.target,local)
                for test in generator.ifs: expression(test,local)
            expression(node.elt,local)
        else:
            for child in ast.iter_child_nodes(node): expression(child,names)

    def statements(block, names):
        for node in block:
            if isinstance(node,ast.Assign):
                expression(node.value,names)
                for item in node.targets: target(item,names)
            elif isinstance(node,ast.For):
                expression(node.iter,names)
                local = names.copy()
                target(node.target,local)
                statements(node.body,local)
                statements(node.orelse,names.copy())
            elif isinstance(node,ast.If):
                expression(node.test,names)
                statements(node.body,names.copy())
                statements(node.orelse,names.copy())
            else:
                expression(node,names)

    statements(tree.body,set())


class Delegation(NotebookTarget, Assignment):
    pass


class Edit(NotebookTarget):
    revision: StrictInt = Field(ge=1)
    action: Literal['insert','replace']
    cell_type: Literal['code','markdown'] = 'code'
    cell_id: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,64}$')
    source: str = Field(min_length=1,max_length=3200)
    explanation: str = Field(min_length=1,max_length=500)

    @model_validator(mode='after')
    def checked(self):
        if not self.source.strip() or not self.explanation.strip() or (self.action=='replace' and not self.cell_id):
            raise ValueError('Provide content, explanation, and a target cell for replacement')
        if self.cell_type=='code': check_python(self.source)
        return self


class Execute(NotebookTarget):
    revision: StrictInt = Field(ge=1)
    cell_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def receipt_view(receipt):
    view = {key:receipt.get(key) for key in ('execution_id','cell_id','state','source_hash','applied_revision')}
    result = receipt.get('result') or {}
    view['status'] = result.get('status')
    view['outputs'] = preview_cell({'id':receipt['cell_id'],'cell_type':'code','source':'',
                                   'outputs':result.get('outputs',[])})['outputs']
    for output in view['outputs']:
        output['text'] = re.sub(r'\x1b\[[0-9;]*m', '', output['text'])
    return view


class CodingTools:
    def __init__(self, store, state):
        self.store, self.state = store, state

    def declarations(self):
        coordinator = frozenset({'coordinator'})
        coding = frozenset({'coding_agent'})
        return [
            Tool('delegate_coding','Request approval to assign the selected approved roadmap task to a Coding Agent.',
                 Delegation,self.delegate,'coding_assignment','coding.delegate','delegate',True,roles=coordinator,
                 idempotency='owned-child'),
            Tool('apply_cell_edit','Propose a revision-bound cell edit. For insert, cell_id is the optional preceding cell.',
                 Edit,self.edit,'coding_edit','coding.edit','write',True,roles=coding,idempotency='notebook-cas'),
            Tool('execute_approved_cell','Request separate approval to run the exact saved code from this run’s most recent cell edit.',
                 Execute,self.execute,'coding_execute','coding.execute','execute',True,timeout=150,roles=coding,
                 idempotency='notebook-receipt'),
        ]

    def delegate(self, run, args, operation):
        child = self.store.delegate(run,operation)
        return ToolResult(data={'child_id':child},provenance={'notebook_id':args.notebook_id,'task_id':args.task_id})

    def _current(self, run, args):
        self.store._fence(run['id'],run['owner'],run['generation'],cancelling=True)
        document = self.store.scope_current(run)
        grant = run['grant_json']
        self.store.task_current(grant['project_id'],args.notebook_id,grant['assignment'])
        if document['metadata']['europa']['revision'] != args.revision:
            raise AgentFault('Notebook revision changed. Review a new cell proposal.',409)
        return document

    def edit(self, run, args, operation):
        app = self.store.app
        with app.notebooks.locked(),app.mutex:
            document = self._current(run,args)
            target = app.notebooks.cell(document,args.cell_id) if args.cell_id else None
            if args.action=='replace' and target['cell_type']!=args.cell_type:
                raise AgentFault('Cell type changed',409)
            index = document['cells'].index(target)+1 if target and args.action=='insert' else len(document['cells'])
            updated = app.notebooks.edit_cell(args.notebook_id,args.revision,'insert' if args.action=='insert' else 'edit',
                args.cell_id,cell_type=args.cell_type,text=args.source,index=index)
            cell = updated['cells'][index] if args.action=='insert' else app.notebooks.cell(updated,args.cell_id)
        return ToolResult(data={'cell_id':cell['id'],'revision':updated['metadata']['europa']['revision'],
                                'source_hash':source_hash(cell),'cell_type':cell['cell_type']},
                          provenance={'notebook_id':args.notebook_id,'task_id':run['grant_json']['assignment']['task_id']})

    async def execute(self, run, args, operation):
        app = self.store.app
        execution = operation.removeprefix('agent-')
        def prepare(notebook, cell_id, revision):
            with app.notebooks.locked(),app.mutex:
                if self.store._run(run['id'])['state']=='cancelling':
                    raise AgentFault('Run stopped before execution was prepared',409)
                document = self._current(run,args)
                cell = app.notebooks.cell(document,cell_id)
                if cell['cell_type']!='code' or source_hash(cell)!=args.source_hash:
                    raise AgentFault('Saved code differs from the approved source',409)
                check_python(''.join(cell['source']))
                return app.notebooks.begin(notebook,cell_id,revision,execution_id=execution)
        try:
            await self.state.notebook_service.submit(args.notebook_id,args.cell_id,args.revision,timeout=60,prepare=prepare)
        except Exception:
            try: await asyncio.to_thread(app.notebooks.execution,args.notebook_id,execution)
            except NotebookError as exc:
                if exc.status_code != 404: raise UncertainEffect() from exc
            else: raise UncertainEffect()
            raise
        while time.time()<run['deadline']:
            receipt = await asyncio.to_thread(app.notebooks.execution,args.notebook_id,execution)
            if receipt['state']=='uncertain': raise UncertainEffect()
            if receipt['state'] in {'applied','conflict'}:
                data = receipt_view(receipt)
                await asyncio.to_thread(self.store.record_child_receipt,run,operation,data)
                return ToolResult(data=data,provenance={'notebook_id':args.notebook_id,
                                  'task_id':run['grant_json']['assignment']['task_id']})
            await asyncio.sleep(.2)
        raise UncertainEffect()

    def executions(self, run):
        with self.store.app.notebooks.locked(),self.store.app.mutex:
            row = self.store._run(run)
            calls = self.store.db.execute("SELECT operation_id,state FROM agent_calls WHERE run_id=? AND tool='execute_approved_cell' AND state IN ('started','completed','uncertain') ORDER BY turn_number",(run,)).fetchall()
            result = []
            for call in calls:
                execution = call['operation_id'].removeprefix('agent-')
                try: receipt = self.store.app.notebooks.execution(row['scope']['resource_id'],execution)
                except NotebookError as exc:
                    if exc.status_code != 404: raise
                    continue
                result.append(receipt_view(receipt))
            return {'items':self.store.redactor.value(result)}

    def reconcile(self, run, body):
        store = self.store
        with store.transaction():
            def work():
                row = store._run(run)
                call = store.db.execute("SELECT id,operation_id FROM agent_calls WHERE run_id=? AND state='uncertain' AND tool='execute_approved_cell'",(run,)).fetchone()
                if row['state']!='needs_reconciliation' or not call:
                    raise AgentFault('There is no uncertain execution receipt to reconcile',409)
                notebook = row['scope']['resource_id']
                execution = call['operation_id'].removeprefix('agent-')
                try: receipt = store.app.notebooks.execution(notebook,execution)
                except NotebookError as exc: raise AgentFault('The matching execution receipt is unavailable',409) from exc
                if receipt['state'] not in {'applied','conflict'}:
                    raise AgentFault('Execution receipt has no verified terminal result',409)
                outcome = ToolResult(data=receipt_view(receipt),provenance={'notebook_id':notebook,
                    'task_id':row['grant_json']['assignment']['task_id']}).model_dump()
                store.db.execute("UPDATE agent_calls SET state='completed',result=?,error=NULL,updated_at=? WHERE id=?",
                                 (encoded(store.redactor.value(outcome)),time.time(),call['id']))
                store._event(run,'execution_reconciled',execution_id=execution,state=receipt['state'])
                store._transition(row,'failed',error='Execution receipt reconciled. The interrupted run has ended.')
                return store._public(store._run(run))
            return store._operation(body.operation_id,'agent/reconcile/'+run,body.model_dump(),work)
