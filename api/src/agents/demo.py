"""Deterministic, rule-based demo provider. It is not an LLM.

It lets the Home Coordinator journey run end to end without credentials:
- an owner message that states an intent ("want to", "create", "set up",
  "analyze", "understand", "forecast", "explore") becomes one
  create_project_notebook proposal named from the message's key words;
- any other message gets a short discussion reply and a question;
- after a tool result it reports the success, denial or conflict summary.

It reads only the unfenced owner message (the last "Owner message:" section the
harness appends) and tool results; fenced workspace/conversation data is ignored.

Notebook Coordinator runs (system prompt contains "Role: Notebook Coordinator")
take a separate branch; rules apply in this order:
1. after a propose_roadmap result: report saved / denied / not saved;
2. a planning request when the context says "Project: none" (outside fences) is
   refused honestly: planning needs a project;
3. a planning request ("plan", "roadmap", "step(s)", "break down") becomes one
   propose_roadmap call: the Markov chain tasks when "markov" appears in the
   message or context, otherwise a generic three-task plan; an existing roadmap
   is revised by reusing its task IDs as keys and appending new tasks;
4. a status request ("status", "left", "blocked", "progress", "done") calls
   read_roadmap and lists task states taken only from that tool result;
5. anything else gets a short reply naming the notebook's cell count and revision.
"""
import json
import re
import uuid

from agents.providers import ModelResponse, Provider, ToolCallRequest

LABEL = 'Deterministic demo provider (not an LLM)'
MARKER = 'Owner message:\n'
INTENT = re.compile(r'\b(want(?:s|ed)? to|create|set ?up|analy[sz]e|understand|forecast|explore)\b', re.IGNORECASE)
# Filler words dropped when deriving a project name from the owner's objective.
STOPWORDS = frozenset('''
a about an and any are as at be been build by can could do does for from get go have help how i i'd i'm in
into is it its just learn let like make me more my of on or our please quick quickly really set setup simple
basic some something that the their them then these this those to try up us use using want wants wanted
we what when where which while who why will with would you your new notebook project analysis analyze analyse
understand forecast explore create small little few data
'''.split())
MAX_WORDS = 4


def owner_message(messages):
    """The trusted owner text: everything after the last harness marker."""
    user = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '') or ''
    return user.rpartition(MARKER)[2].strip() if MARKER in user else user.strip()


def key_words(text):
    words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", text)
    return [w for w in words if w.lower() not in STOPWORDS and "'" not in w]


def proposal_names(text):
    """Return (project_name, notebook_name) or None when no name can be derived."""
    match = None
    for match in INTENT.finditer(text):
        pass
    tail = text[match.end():] if match else text
    words = key_words(tail) or key_words(text)
    if not words:
        return None
    words = words[:MAX_WORDS]
    project = ' '.join(w if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:].lower() for w in words)[:80].strip()
    slug = '-'.join(w.lower() for w in words)[:74].strip('-')
    if not project or not slug:
        return None
    return project, f'{slug}.ipynb'


def _result(messages):
    """The tool result the model must answer, if the conversation ends with one."""
    if not messages or messages[-1]['role'] != 'tool':
        return None
    try:
        payload = json.loads(messages[-1]['content'])
    except (TypeError, ValueError):
        return {'status': 'error', 'summary': 'The tool returned an unreadable result.'}
    return payload if isinstance(payload, dict) else {'status': 'error', 'summary': str(payload)}


def report(result):
    status, summary = result.get('status', 'ok'), (result.get('summary') or '').strip()
    if status == 'ok':
        return f'{summary}. Use Open notebook to start working in it.' if summary.startswith('Created ') else summary or 'Done.'
    if status == 'denied':
        return 'Understood, you denied the proposal. Nothing was created. Tell me if you want a different name or approach.'
    if status in {'expired', 'cancelled'}:
        return f'The proposal {status} before it was approved. Nothing was created.'
    if status == 'conflict':
        return f'I could not create it: {summary} Nothing was overwritten. Choose another name and I will propose again.'
    return f'The action did not complete ({status}): {summary} Nothing else was attempted.'


# --------------------------------------------------------------------------- Notebook Coordinator branch

NOTEBOOK_ROLE = 'Role: Notebook Coordinator'
PLAN = re.compile(r'\b(plan|roadmap|steps?|break down)', re.IGNORECASE)
STATUS = re.compile(r'\b(status|left|blocked|progress|done)\b', re.IGNORECASE)
FENCE = re.compile(r'<untrusted source="([^"]*)">\n(.*?)\n</untrusted>', re.DOTALL)
MARKOV_TASKS = (
    ('matrix', 'Define a transition matrix', 'A square transition matrix whose rows sum to 1 is defined'),
    ('simulate', 'Simulate transitions', 'A sequence of states is simulated from the matrix'),
    ('stationary', 'Estimate the stationary distribution', 'The stationary distribution is computed and compared with the simulation'),
    ('convergence', 'Visualize convergence', 'A plot shows state frequencies converging to the stationary distribution'),
)
GENERIC_TASKS = (
    ('inspect', 'Inspect the data', 'The data source, shape and columns are described'),
    ('explore', 'Explore key relationships', 'Key relationships are shown with summaries or plots'),
    ('summarize', 'Summarize findings', 'Findings are summarized in a Markdown cell'),
)
STEP_PREFIX = re.compile(r'^(?:please\s+)?(?:add|include|append)\s+(?:a|an|another|one more)?\s*(?:new\s+)?'
                         r'(?:step|task)s?\s*(?:to\s+|for\s+|that\s+)?', re.IGNORECASE)


def _system(messages):
    return next((m['content'] or '' for m in messages if m['role'] == 'system'), '')


def _context(messages):
    """The harness-assembled context (everything before the owner message)."""
    user = next((m['content'] for m in messages if m['role'] == 'user'), '') or ''
    return user.rpartition(MARKER)[0] if MARKER in user else ''


def _fenced(context, label):
    return next((body for source, body in FENCE.findall(context) if source == label), None)


def _unfenced(context):
    return FENCE.sub('', context)


def _last_tool(messages):
    """Name of the tool whose result ends the conversation."""
    call_id = messages[-1].get('tool_call_id')
    for message in reversed(messages):
        for item in message.get('tool_calls') or []:
            if item['id'] == call_id:
                return item['name']
    return None


def _call(name, content, arguments=None):
    return ModelResponse(content=content, finish_reason='tool_calls', tool_calls=[
        ToolCallRequest(id='demo_' + uuid.uuid4().hex[:12], name=name, arguments=json.dumps(arguments or {}))])


def _step_title(text):
    title = STEP_PREFIX.sub('', text.strip()).strip().rstrip('.!?').strip()
    return (title[:1].upper() + title[1:])[:200] if title else 'Next step'


def plan_arguments(text, context, roadmap):
    """propose_roadmap arguments: a new plan, or a revision of `roadmap` (a roadmap view)."""
    template = MARKOV_TASKS if 'markov' in (text + context).lower() else GENERIC_TASKS
    existing = (roadmap or {}).get('tasks') or []
    ids = {t['task_id'] for t in existing}
    tasks = [{'key': t['task_id'], 'title': t['title'], 'acceptance_criteria': t['acceptance_criteria'],
              'depends_on': [d for d in t['dependencies'] if d in ids]} for t in existing]
    titles = {t['title'].casefold() for t in existing}
    new = [(key, title, [criterion]) for key, title, criterion in template if title.casefold() not in titles]
    if existing and not new:
        title = _step_title(text)
        new = [('step', title, [f'The notebook shows the result of: {title}'])]
    previous = tasks[-1]['key'] if tasks else None
    for key, title, criteria in new:
        while key in ids: key += '-n'
        ids.add(key)
        tasks.append({'key': key, 'title': title, 'acceptance_criteria': criteria,
                      'depends_on': [previous] if previous else []})
        previous = key
    objective = roadmap['objective'] if roadmap else (text.strip()[:2000] or 'Plan the work in this notebook')
    return {'objective': objective, 'tasks': tasks, 'rationale': 'Deterministic demo plan (not an LLM).'}, len(existing), len(new)


def _propose(text, context, roadmap):
    arguments, kept, added = plan_arguments(text, context, roadmap)
    if roadmap:
        note = (f'I propose revising the roadmap: keep its {kept} existing tasks and add {added}. '
                'Please review the plan; nothing is saved until you approve it.')
    else:
        note = (f'I propose a roadmap with {added} tasks: ' + ' → '.join(t['title'] for t in arguments['tasks'])
                + '. Please review the plan; nothing is saved until you approve it.')
    return _call('propose_roadmap', note, arguments)


def plan_report(result):
    status, summary = result.get('status', 'ok'), (result.get('summary') or '').strip()
    if status == 'ok':
        counted = re.search(r'with (\d+) tasks', summary)
        count = counted.group(1) if counted else len(((result.get('data') or {}).get('tasks')) or [])
        return f'Saved the roadmap with {count} tasks. Accept tasks in the Roadmap panel as you verify them.'
    if status == 'denied':
        return 'No plan was saved.'
    return f'The plan was not saved: {summary}'


def status_report(result):
    """Task states quoted only from a read_roadmap result."""
    if result.get('status', 'ok') != 'ok':
        return f"I could not read the roadmap: {(result.get('summary') or '').strip()}"
    data = result.get('data') or {}
    if data.get('project_id') is None:
        return 'This notebook is not in a project, so it has no roadmap. Planning needs a project.'
    roadmap = data.get('roadmap')
    if not roadmap:
        return 'There is no roadmap for this notebook yet. Ask me to plan it and I will propose one for your approval.'
    tasks = roadmap.get('tasks') or []
    by_id = {t['task_id']: t for t in tasks}
    lines = [f"Roadmap revision {roadmap['revision']} ({roadmap['progress']:g}% complete):"]
    for task in tasks:
        line = f"- {task['title']}: {task['status'].replace('_', ' ')}"
        if task.get('explanation'): line += f" ({task['explanation']})"
        waiting = [by_id[d]['title'] for d in task['dependencies'] if d in by_id and by_id[d]['status'] != 'completed']
        if task['status'] not in {'completed', 'cancelled'} and waiting:
            line += ' — waits for ' + ', '.join(waiting)
        lines.append(line)
    lines.append('Only tasks marked completed in the roadmap are done. You accept work in the Roadmap panel.')
    return '\n'.join(lines)


def notebook_turn(messages, available):
    text, context = owner_message(messages), _context(messages)
    standalone = 'Project: none' in _unfenced(context)
    result = _result(messages)
    if result is not None:
        tool = _last_tool(messages)
        if tool == 'propose_roadmap':
            return ModelResponse(content=plan_report(result), finish_reason='stop')
        if tool == 'read_roadmap' and PLAN.search(text) and result.get('status') == 'ok' \
                and (result.get('data') or {}).get('project_id') and 'propose_roadmap' in available:
            return _propose(text, context, (result.get('data') or {}).get('roadmap'))
        if tool == 'read_roadmap':
            return ModelResponse(content=status_report(result), finish_reason='stop')
        return ModelResponse(content=(result.get('summary') or 'Done.').strip(), finish_reason='stop')
    if standalone and PLAN.search(text):
        return ModelResponse(finish_reason='stop', content=(
            'Planning needs a project. This notebook is not in a project, so I cannot create a roadmap for it. '
            'Add the notebook to a project and ask again; meanwhile we can still discuss its cells.'))
    if PLAN.search(text) and 'propose_roadmap' in available:
        fenced = _fenced(context, 'roadmap')
        if fenced is None or fenced == 'No roadmap yet.':
            return _propose(text, context, None)
        try:
            roadmap = json.loads(fenced)
        except ValueError:  # Context was truncated: read the authoritative roadmap first.
            return _call('read_roadmap', 'Reading the current roadmap before proposing a revision.')
        return _propose(text, context, roadmap if isinstance(roadmap, dict) else None)
    if STATUS.search(text) and 'read_roadmap' in available:
        return _call('read_roadmap', 'Reading the roadmap.')
    source = next((label for label, _ in FENCE.findall(context) if label.startswith('notebook:')), '')
    revision = source.rpartition('@')[2] or 'unknown'
    count = re.search(r'"cell_count": (\d+)', _fenced(context, source) or '')
    cells = int(count.group(1)) if count else None
    described = (f"This notebook has {cells} cell{'s' if cells != 1 else ''} and is at revision {revision}."
                 if cells is not None else f'This notebook is at revision {revision}.')
    follow = ('It is not in a project, so planning needs a project first.' if standalone
              else 'Ask me to plan next steps or for the roadmap status.')
    return ModelResponse(finish_reason='stop', content=(
        f'{described} {follow} I can discuss it with you, but I cannot edit cells or run code in this phase.'))


class DemoProvider(Provider):
    name, model = 'demo', 'deterministic-demo'
    label = LABEL

    async def complete(self, messages, tools, *, max_tokens):
        if NOTEBOOK_ROLE in _system(messages):
            return notebook_turn(messages, {t['name'] for t in tools})
        result = _result(messages)
        if result is not None:
            return ModelResponse(content=report(result).strip(), finish_reason='stop')
        text = owner_message(messages)
        available = {t['name'] for t in tools}
        names = proposal_names(text) if INTENT.search(text) else None
        if names and 'create_project_notebook' in available:
            project, notebook = names
            arguments = {'project_name': project, 'notebook_name': notebook,
                         'purpose': f'Owner objective: {text[:900]}'}
            return ModelResponse(
                content=f'I can set up a project folder {project}/ with the notebook {project}/{notebook}. '
                        'Please review the proposal.',
                tool_calls=[ToolCallRequest(id='demo_' + uuid.uuid4().hex[:12], name='create_project_notebook',
                                            arguments=json.dumps(arguments))],
                finish_reason='tool_calls')
        terms = ', '.join(key_words(text)[:MAX_WORDS])
        return ModelResponse(
            content=('This sounds like something to discuss before building anything.'
                     + (f' Key terms I noticed: {terms}.' if terms else '')
                     + ' What data do you have, and what question should the answer settle?'
                     ' Would you like me to set up a notebook for this? If so, tell me what you want to analyze or explore.'),
            finish_reason='stop')
