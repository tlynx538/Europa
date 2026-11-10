"""Provider-neutral function calling. No SDK executes tools or authorizes actions."""
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import aiohttp
from agents.models import ModelReply, ToolCall


class ProviderFailure(Exception):
    pass


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def text(self, value):
        for secret in self.secrets: value = value.replace(secret, '[redacted]')
        return re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}', r'\1[redacted]', value)

    def value(self, value):
        if isinstance(value, str): return self.text(value)
        if isinstance(value, list): return [self.value(v) for v in value]
        if isinstance(value, dict): return {self.text(k): self.value(v) for k, v in value.items()}
        return value


@dataclass(frozen=True)
class ProviderConfig:
    kind: str = 'disabled'
    model: str = ''
    base_url: str = ''
    api_key: str = field(default='', repr=False)
    supports_tools: bool = True
    token_parameter: str = 'max_tokens'
    timeout: float = 30

    @classmethod
    def environment(cls):
        kind = os.getenv('EUROPA_AGENT_PROVIDER', 'disabled')
        if kind not in {'disabled', 'fake', 'openai_compatible', 'openrouter'}: raise ValueError('Unsupported agent provider')
        defaults = {'openai_compatible': 'https://api.openai.com/v1', 'openrouter': 'https://openrouter.ai/api/v1'}
        return cls(kind, os.getenv('EUROPA_AGENT_MODEL', 'fake-v1' if kind == 'fake' else ''),
                   os.getenv('EUROPA_AGENT_BASE_URL', defaults.get(kind, '')),
                   os.getenv('EUROPA_AGENT_API_KEY', ''), os.getenv('EUROPA_AGENT_TOOLS', '1') == '1',
                   os.getenv('EUROPA_AGENT_TOKEN_PARAMETER', 'max_tokens'))

    def public(self):
        return {'provider': self.kind, 'model': self.model, 'enabled': self.kind != 'disabled',
                'supports_tools': self.supports_tools, 'test_provider': self.kind == 'fake'}


class DisabledProvider:
    config = ProviderConfig()

    async def complete(self, messages, tools, output_limit):
        raise ProviderFailure('Agent provider is not configured')


class ChatProvider:
    def __init__(self, config):
        self.config = config
        url = urlsplit(config.base_url)
        if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'})) or url.username or url.password or url.query or url.fragment:
            raise ValueError('Agent endpoint must be HTTPS or loopback HTTP without embedded credentials')
        if not config.api_key or not config.model or len(config.model) > 128: raise ValueError('Agent model and server credential required')
        if config.token_parameter not in {'max_tokens', 'max_completion_tokens'}: raise ValueError('Unsupported token limit parameter')
        if not 0 < config.timeout <= 60: raise ValueError('Unbounded provider timeout')

    async def complete(self, messages, tools, output_limit):
        body = {'model': self.config.model, 'messages': messages, 'stream': False, self.config.token_parameter: output_limit}
        if self.config.supports_tools and tools: body['tools'] = tools
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout), trust_env=False) as http:
                async with http.post(self.config.base_url.rstrip('/') + '/chat/completions',
                                     json=body, headers={'Authorization': 'Bearer ' + self.config.api_key}, allow_redirects=False) as response:
                    if response.status != 200: raise ProviderFailure('Model request failed')
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(8192):
                        size += len(chunk)
                        if size > 131072: raise ProviderFailure('Model response exceeds limit')
                        chunks.append(chunk)
            data = json.loads(b''.join(chunks))
            choice = data['choices'][0]
            if choice.get('finish_reason') in {'length', 'content_filter'}: raise ProviderFailure('Incomplete model reply')
            message, usage = choice['message'], data.get('usage', {})
            calls = message.get('tool_calls') or []
            if calls and not self.config.supports_tools: raise ProviderFailure('Unexpected tool response')
            validated = []
            for call in calls:
                if call.get('type') != 'function': raise ProviderFailure('Unsupported model tool format')
                raw = call['function']['arguments']
                if not isinstance(raw, str) or len(raw.encode()) > 16384: raise ProviderFailure('Tool argument limit exceeded')
                validated.append(ToolCall(id=call['id'], name=call['function']['name'], arguments=json.loads(raw)))
            # Deliberately discard reasoning, logprobs, headers and all other fields.
            return ModelReply(content=message.get('content') or '', calls=validated,
                              input_tokens=usage.get('prompt_tokens', 0), output_tokens=usage.get('completion_tokens', 0))
        except ProviderFailure: raise
        except Exception as exc: raise ProviderFailure('Invalid or unavailable model response') from exc


class FakeProvider:
    """Explicit deterministic test provider. No claim of external model intelligence."""
    config = ProviderConfig('fake', 'fake-v1')

    async def complete(self, messages, tools, output_limit):
        tool_messages = [m for m in messages if m['role'] == 'tool']
        if tools and not tool_messages:
            return ModelReply(calls=[ToolCall(id='catalog_read', name='list_projects', arguments={})])
        return ModelReply(content='Start with probability, conditional probability, and matrix multiplication. Then learn states, transition probabilities, simulation, and stationary distributions. A simple two-state Markov chain is a useful first notebook example.')


def configured_provider(config=None):
    config = config or ProviderConfig.environment()
    if config.kind == 'disabled': return DisabledProvider()
    if config.kind == 'fake': return FakeProvider()
    return ChatProvider(config)
