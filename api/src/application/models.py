"""Bounded public application contracts. Authorship is assigned by the server."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Identity = str
Status = Literal['pending', 'in_progress', 'blocked', 'needs_input', 'completed', 'cancelled']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Mutation(Model):
    operation_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class Revision(Mutation):
    revision: StrictInt = Field(ge=1)


class ProjectCreate(Mutation):
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=10000)


class ProjectUpdate(ProjectCreate):
    revision: StrictInt = Field(ge=1)
    status: Literal['active', 'archived', 'deleted'] = 'active'


class Membership(Revision):
    notebook_id: str
    associated: bool = True


class Scoped(Mutation):
    project_id: str
    notebook_id: str | None = None


class ConversationCreate(Scoped):
    title: str = Field(default='Discussion', min_length=1, max_length=256)


class Links(Model):
    task_id: str | None = None
    message_id: str | None = None
    notebook_id: str | None = None
    cell_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    document_revision: StrictInt | None = Field(default=None, ge=1)
    execution_receipt_id: str | None = None


class MessageCreate(Mutation, Links):
    content: str = Field(min_length=1, max_length=32000)
    parent_id: str | None = None
    state: Literal['published', 'needs_input'] = 'published'


class Evidence(Model):
    summary: str = Field(min_length=1, max_length=2000)
    message_id: str | None = None
    execution_receipt_id: str | None = None


class Task(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    title: str = Field(min_length=1, max_length=256)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=32)
    parent_id: str | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=128)
    status: Status = 'pending'
    explanation: str | None = Field(default=None, max_length=2000)
    weight: float = Field(default=1, gt=0, le=1000, allow_inf_nan=False)
    links: Links = Field(default_factory=Links)
    evidence: list[Evidence] = Field(default_factory=list, max_length=32)
    accepted: bool = False

    @model_validator(mode='after')
    def bounded(self):
        if any(not v.strip() or len(v) > 2000 for v in self.acceptance_criteria):
            raise ValueError('Acceptance criteria must be nonblank and bounded')
        if self.status in {'blocked', 'needs_input'} and not self.explanation:
            raise ValueError('Blocked/needs_input tasks require an explanation')
        if self.status == 'completed' and (not self.accepted or not self.evidence):
            raise ValueError('Completion requires owner acceptance and evidence')
        if self.accepted and self.status != 'completed':
            raise ValueError('Only completed tasks may be accepted')
        return self


class RoadmapCreate(Scoped):
    objective: str = Field(min_length=1, max_length=10000)
    tasks: list[Task] = Field(default_factory=list, max_length=256)


class RoadmapUpdate(Revision):
    objective: str = Field(min_length=1, max_length=10000)
    tasks: list[Task] = Field(max_length=256)


class TaskUpdate(Revision):
    task: Task


class CodeRange(Model):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)


class CommentCreate(Scoped):
    cell_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    document_revision: StrictInt | None = Field(default=None, ge=1)
    code_range: CodeRange | None = None
    source_fingerprint: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    content: str = Field(min_length=1, max_length=32000)
    parent_id: str | None = None
    classification: Literal['question', 'suggestion', 'change_request', 'stop_request'] = 'question'


class Resolve(Revision):
    resolved: bool


class ActivityCreate(Scoped, Links):
    # Public ingress permits only user_intervention; other types are server-only.
    event_type: Literal['task_created', 'plan_approved', 'task_started', 'cell_added',
        'cell_updated', 'execution_requested', 'execution_succeeded', 'execution_failed',
        'coordinator_review_requested', 'task_blocked', 'task_completed', 'user_intervention'] = 'user_intervention'
    summary: str = Field(min_length=1, max_length=2000)
    result: Literal['recorded', 'succeeded', 'failed', 'blocked'] = 'recorded'


AuthorRole = Literal['user', 'coordinator', 'coding_agent', 'system/tool']


class NotebookMember(Model):
    notebook_id: str
    present: bool


class ProjectData(Model):
    title: str
    description: str | None
    status: Literal['active', 'archived', 'deleted']
    file_references: list[str]
    notebooks: list[NotebookMember]


class ConversationData(Model):
    title: str


class MessageData(Links):
    content: str
    parent_id: str | None
    state: Literal['published', 'needs_input']
    author_role: AuthorRole
    conversation_id: str


class TaskDisplay(Model):
    compact: bool
    checkmark: bool
    show_progress: bool


class StoredTask(Task):
    accepted_by: AuthorRole | None = None


class TaskView(StoredTask):
    progress: float = Field(ge=0, le=100)
    display: TaskDisplay


class OverallDisplay(Model):
    label: str
    percentage_position: Literal['right']


class RoadmapData(Model):
    objective: str
    tasks: list[TaskView]
    progress: float = Field(ge=0, le=100)
    overall_display: OverallDisplay


class CommentData(Model):
    project_id: str
    notebook_id: str
    cell_id: str
    document_revision: int
    code_range: CodeRange | None
    source_fingerprint: str
    content: str
    parent_id: str | None
    classification: Literal['question', 'suggestion', 'change_request', 'stop_request']
    author_role: AuthorRole
    resolved: bool
    thread_id: str
    anchor_status: Literal['current', 'outdated', 'orphaned']


class ActivityData(Links):
    project_id: str
    event_type: str
    summary: str
    result: Literal['recorded', 'succeeded', 'failed', 'blocked']
    actor: AuthorRole


class Record(Model):
    id: str
    project_id: str | None
    notebook_id: str | None
    revision: int
    created_at: float
    updated_at: float
    sequence: int
    data: ProjectData | ConversationData | MessageData | RoadmapData | CommentData | ActivityData


class Page(Model):
    items: list[Record]
    next_cursor: int | None


class PlanSnapshot(Model):
    objective: str
    tasks: list[StoredTask]


class PlanVersion(Model):
    revision: int
    created_at: float
    data: PlanSnapshot


class VersionPage(Model):
    items: list[PlanVersion]
    next_cursor: int | None
