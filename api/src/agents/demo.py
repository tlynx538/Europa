"""Deterministic, rule-based demo provider. It is not an LLM.

It lets the Home Coordinator journey run end to end without credentials:
- an owner message that states an intent ("want to", "create", "set up",
  "analyze", "understand", "forecast", "explore") becomes one
  create_project_notebook proposal named from the message's key words;
- any other message gets a short discussion reply and a question;
- after a tool result it reports the success, denial or conflict summary.

It reads only the unfenced owner message (the last "Owner message:" section the
harness appends) and tool results; fenced workspace/conversation data is ignored.
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


class DemoProvider(Provider):
    name, model = 'demo', 'deterministic-demo'
    label = LABEL

    async def complete(self, messages, tools, *, max_tokens):
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
