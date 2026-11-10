"""Bounded owner ingress; models cannot invoke these control-plane contracts."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Identity = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
Operation = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Scope(Model):
    kind: Literal['workspace', 'project', 'notebook'] = 'workspace'
    resource_id: Identity | None = None
    directory: str = Field(default='', max_length=4096)

    @model_validator(mode='after')
    def resource(self):
        if (self.kind == 'workspace') != (self.resource_id is None):
            raise ValueError('Scope resource required only for project/notebook')
        return self


class Budgets(Model):
    requests: StrictInt = Field(default=6, ge=1, le=12)
    steps: StrictInt = Field(default=16, ge=1, le=64)
    tools: StrictInt = Field(default=8, ge=1, le=32)
    wall_seconds: StrictInt = Field(default=300, ge=1, le=900)
    context_tokens: StrictInt = Field(default=8192, ge=1024, le=32768)
    output_tokens: StrictInt = Field(default=4096, ge=1, le=8192)
    total_tokens: StrictInt = Field(default=64000, ge=1024, le=128000)
    repairs: StrictInt = Field(default=1, ge=0, le=2)
    depth: StrictInt = Field(default=1, ge=0, le=2)


class RunCreate(Model):
    operation_id: Operation
    scope: Scope = Field(default_factory=Scope)
    mode: Literal['discuss', 'coordinate'] = 'discuss'
    content: str = Field(min_length=1, max_length=8000)
    authorized_notebooks: list[Identity] = Field(default_factory=list, max_length=20)
    budgets: Budgets = Field(default_factory=Budgets)
    cell_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    document_revision: StrictInt | None = Field(default=None, ge=1)
    parent_id: Identity | None = None

    @model_validator(mode='after')
    def nonblank(self):
        if not self.content.strip(): raise ValueError('Message is blank')
        if (self.cell_id is None) != (self.document_revision is None):
            raise ValueError('A cell reference requires its document revision')
        if self.scope.kind != 'notebook' and (self.cell_id or self.parent_id):
            raise ValueError('Agent message references require notebook scope')
        return self


class Decision(Model):
    operation_id: Operation
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['granted', 'denied']


class Control(Model):
    operation_id: Operation


class ToolCall(Model):
    id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_]+$')
    arguments: dict


class ModelReply(Model):
    content: str = Field(default='', max_length=16000)
    calls: list[ToolCall] = Field(default_factory=list, max_length=1)
    input_tokens: StrictInt = Field(default=0, ge=0, le=1000000)
    output_tokens: StrictInt = Field(default=0, ge=0, le=1000000)

    @model_validator(mode='after')
    def present(self):
        if not self.content.strip() and not self.calls: raise ValueError('Empty model reply')
        return self
