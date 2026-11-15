"""Provider-neutral model adapters.

Messages use the OpenAI chat shape internally:
  {"role": "system"|"user"|"assistant"|"tool", "content": str,
   "tool_calls": [{"id", "name", "arguments"}], "tool_call_id": str}
Adapters never log, persist or return credentials. Provider reasoning fields are
discarded; only visible content and tool calls are returned to the harness.
"""
from dataclasses import dataclass, field
import ipaddress
import json
import os
from urllib.parse import urlparse

import httpx


class ProviderError(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # Raw JSON text from the model; the harness validates it.


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


class Provider:
    name = 'abstract'
    model = 'none'
    supports_tools = True

    async def complete(self, messages, tools, *, max_tokens):  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self):
        pass


def _wire_messages(messages):
    wire = []
    for message in messages:
        item = {'role': message['role'], 'content': message.get('content') or ''}
        if message.get('tool_calls'):
            item['tool_calls'] = [{'id': call['id'], 'type': 'function',
                                   'function': {'name': call['name'], 'arguments': call['arguments']}}
                                  for call in message['tool_calls']]
            if not item['content']: item['content'] = None
        if message['role'] == 'tool':
            item['tool_call_id'] = message['tool_call_id']
        wire.append(item)
    return wire


class OpenAICompatibleProvider(Provider):
    """Chat Completions with function tools. Works with OpenAI, OpenRouter, vLLM, Ollama."""

    def __init__(self, *, base_url, api_key, model, name='openai', headers=None,
                 transport=None, timeout=90.0, supports_tools=True):
        parsed = urlparse(base_url)
        loopback = False
        try:
            loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            loopback = parsed.hostname == 'localhost'
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
            raise ProviderError('Provider base URL must use HTTPS (plain HTTP is allowed only on loopback)')
        if not model:
            raise ProviderError('A model name is required')
        self.name, self.model, self.supports_tools = name, model, supports_tools
        self._url = base_url.rstrip('/') + '/chat/completions'
        auth = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport,
                                         headers={**auth, **(headers or {})}, follow_redirects=False)

    async def complete(self, messages, tools, *, max_tokens):
        body = {'model': self.model, 'messages': _wire_messages(messages), 'max_tokens': max_tokens}
        if tools and self.supports_tools:
            body['tools'] = [{'type': 'function', 'function': spec} for spec in tools]
            body['tool_choice'] = 'auto'
        try:
            response = await self._client.post(self._url, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError('Model request timed out', retryable=True) from None
        except httpx.HTTPError as exc:
            raise ProviderError(f'Model request failed: {type(exc).__name__}', retryable=True) from None
        if response.status_code != 200:
            # Never echo response bodies verbatim: some providers reflect request headers.
            raise ProviderError(f'Model provider returned HTTP {response.status_code}',
                                retryable=response.status_code in {408, 429, 500, 502, 503, 504})
        try:
            payload = response.json()
            choice = payload['choices'][0]
            message = choice['message']
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError('Model provider returned an unrecognized response') from None
        calls = []
        for index, call in enumerate(message.get('tool_calls') or []):
            function = call.get('function') or {}
            arguments = function.get('arguments', '{}')
            if not isinstance(arguments, str): arguments = json.dumps(arguments)
            calls.append(ToolCallRequest(id=str(call.get('id') or f'call_{index}'),
                                         name=str(function.get('name', '')), arguments=arguments))
        usage = payload.get('usage') or {}
        return ModelResponse(content=message.get('content') or '', tool_calls=calls,
                             usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                    if isinstance(usage.get(k), int)},
                             finish_reason=choice.get('finish_reason'))

    async def close(self):
        await self._client.aclose()


class FakeProvider(Provider):
    """Deterministic test provider: a script of responses or a callable(messages, tools)."""
    name, model = 'fake', 'fake-model'

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def complete(self, messages, tools, *, max_tokens):
        self.calls.append({'messages': messages, 'tools': [t['name'] for t in tools], 'max_tokens': max_tokens})
        if callable(self.script):
            result = self.script(messages, tools)
            if hasattr(result, '__await__'): result = await result
            return result
        if not self.script:
            raise ProviderError('Fake provider script exhausted')
        item = self.script.pop(0)
        if isinstance(item, Exception): raise item
        return item


def configured_provider(environ=None):
    """Return (provider|None, reason). Credentials are read from the environment only."""
    env = os.environ if environ is None else environ
    selection = env.get('EUROPA_AGENT_PROVIDER', '').strip().lower()
    if not selection:
        return None, 'No agent provider configured (set EUROPA_AGENT_PROVIDER)'
    try:
        if selection == 'demo':
            from agents.demo import DemoProvider
            return DemoProvider(), None
        if selection in {'openai', 'openai-compatible'}:
            return OpenAICompatibleProvider(
                base_url=env.get('EUROPA_AGENT_BASE_URL', 'https://api.openai.com/v1'),
                api_key=env.get('EUROPA_AGENT_API_KEY') or env.get('OPENAI_API_KEY', ''),
                model=env.get('EUROPA_AGENT_MODEL', ''), name='openai-compatible'), None
        if selection == 'openrouter':
            key = env.get('EUROPA_AGENT_API_KEY') or env.get('OPENROUTER_API_KEY', '')
            if not key: return None, 'OpenRouter requires OPENROUTER_API_KEY or EUROPA_AGENT_API_KEY'
            return OpenAICompatibleProvider(
                base_url=env.get('EUROPA_AGENT_BASE_URL', 'https://openrouter.ai/api/v1'), api_key=key,
                model=env.get('EUROPA_AGENT_MODEL', ''), name='openrouter',
                headers={'X-Title': 'Europa'}), None
    except ProviderError as exc:
        return None, str(exc)
    return None, f'Unknown agent provider {selection!r}'
