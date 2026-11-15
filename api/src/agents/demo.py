"""Placeholder; replaced by the deterministic demo provider in 4B."""
from agents.providers import ModelResponse, Provider


class DemoProvider(Provider):
    name, model = 'demo', 'deterministic-demo'

    async def complete(self, messages, tools, *, max_tokens):
        return ModelResponse(content='Demo provider.')
