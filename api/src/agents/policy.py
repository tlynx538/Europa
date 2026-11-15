"""Server-side role capabilities, tool exposure and budgets. Models cannot alter these."""
from dataclasses import dataclass, field, asdict

from agents.tools import REGISTRY


@dataclass(frozen=True)
class Budget:
    model_calls: int = 8
    tool_calls: int = 16
    steps: int = 24
    active_seconds: float = 600.0
    max_output_tokens: int = 2048
    context_chars: int = 24000
    repairs: int = 2

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Role:
    name: str
    author: str
    capabilities: frozenset
    tools: tuple
    scope: str  # workspace | notebook
    budget: Budget = field(default_factory=Budget)


ROLES = {
    'home_coordinator': Role(
        'home_coordinator', 'coordinator', frozenset({'read:workspace', 'propose:workspace'}),
        ('list_workspace', 'list_projects', 'list_notebooks', 'read_notebook_summary', 'create_project_notebook'),
        'workspace', Budget(model_calls=6, tool_calls=10)),
    'notebook_coordinator': Role(
        'notebook_coordinator', 'coordinator', frozenset({'read:notebook', 'read:roadmap', 'propose:roadmap', 'delegate'}),
        ('read_notebook', 'read_roadmap', 'read_execution', 'propose_roadmap', 'delegate_task'),
        'notebook'),
    'coding_agent': Role(
        'coding_agent', 'coding_agent', frozenset({'read:notebook', 'edit:notebook', 'execute:notebook'}),
        ('read_notebook', 'read_execution', 'insert_code_cell', 'insert_markdown_cell', 'replace_cell_source', 'execute_cell'),
        'notebook', Budget(model_calls=10, tool_calls=16)),
}

MAX_DELEGATION_DEPTH = 1
MAX_CONCURRENT_RUNS = 2
APPROVAL_TTL_SECONDS = 30 * 60


class PolicyDenied(Exception):
    pass


class PolicyEngine:
    def __init__(self, roles=ROLES, registry=REGISTRY):
        self.roles, self.registry = roles, registry

    def role(self, name):
        try: return self.roles[name]
        except KeyError: raise PolicyDenied(f'Unknown role {name!r}') from None

    def tools_for(self, role_name):
        role = self.role(role_name)
        return [self.registry[name] for name in role.tools]

    def check(self, run, tool_name):
        """Return the tool if this run's role may call it; otherwise raise PolicyDenied."""
        role = self.role(run['role'])
        tool = self.registry.get(tool_name)
        if tool is None:
            raise PolicyDenied(f'Unknown tool {tool_name!r}')
        if tool_name not in role.tools or tool.capability not in role.capabilities:
            raise PolicyDenied(f'{role.name} is not permitted to use {tool_name}')
        if role.scope == 'notebook' and not run.get('notebook_id'):
            raise PolicyDenied('Notebook-scoped role has no notebook')
        if tool.capability == 'delegate' and run['depth'] >= MAX_DELEGATION_DEPTH:
            raise PolicyDenied('Delegation depth limit reached')
        return tool
