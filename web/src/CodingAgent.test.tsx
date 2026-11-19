// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Workspace } from './Workspace'
import { ApprovalCard } from './AgentRun'
import type { ApprovalView } from './agentApi'

// Phase 4D Stage 2: owner assignment, the assignment message and its run, and guarded approvals.
vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {}, settingsOpen: false, openSettings: () => {}, closeSettings: () => {} }),
}))
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

const view = (task: Record<string, unknown>) => ({ parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1, links: {}, evidence: [], accepted: false,
  acceptance_criteria: ['Rows sum to 1'], progress: 0, display: { compact: false, checkmark: false, show_progress: false }, ...task })
const roadmap = (id: string, notebook: string, tasks: Record<string, unknown>[]) => ({ id, revision: 4, notebook_id: notebook, created_at: 1,
  data: { objective: 'Understand a Markov chain', progress: 0, overall_display: { label: 'Overall progress', percentage_position: 'right' }, tasks: tasks.map(view) } })
const MAIN = roadmap('r1', 'first', [
  { id: 't1', title: 'Define a transition matrix' },
  { id: 't2', title: 'Simulate transitions', dependencies: ['t1'] },
  { id: 't3', title: 'Already going', status: 'in_progress' },
  { id: 't4', title: 'Finished', status: 'completed', accepted: true, evidence: [{ summary: 'ok' }] },
])
const OTHER = roadmap('r2', 'other', [{ id: 'o1', title: 'Other notebook task' }])
const message = (id: string, author: string, content: string) =>
  ({ id, notebook_id: 'first', revision: 1, created_at: 1700000000, data: { author_role: author, content, parent_id: null, state: 'published', placement_after_cell_id: null } })
const approval = (tool: string, preview: Record<string, unknown>): ApprovalView =>
  ({ id: `a-${tool}`, tool, state: 'pending', digest: 'd'.repeat(64), expires_at: 1900000000, preview } as ApprovalView)
const coderRun = (state: string, approvals: ApprovalView[] = []) => ({ id: 'k1', role: 'coding_agent', state, conversation_id: 'canonical', trigger_message_id: 'assigned',
  parent_run_id: null, depth: 1, notebook_id: 'first', project_id: 'p1', provider: 'demo', model: 'm', error: null, result: null, created_at: 1, updated_at: 1, finished_at: null,
  task: { roadmap_id: 'r1', task_id: 't1', title: 'Define a transition matrix', assigned_by: 'user' }, tool_calls: [], approvals })

type Call = { url: string; method: string; body: Record<string, unknown> | null }
function backend({ enabled = true, messages = [] as unknown[], runs = [] as unknown[] } = {}) {
  const calls: Call[] = []
  const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET', body = init?.body ? JSON.parse(init.body as string) : null
    calls.push({ url, method, body })
    if (url === '/api/agent/status') return reply({ enabled, provider: enabled ? 'demo' : null, model: null, reason: enabled ? null : 'off' })
    if (url.startsWith('/api/projects?')) return reply({ items: [{ id: 'p1', revision: 1, notebook_id: null, created_at: 1, data: { title: 'Markov', status: 'active', notebooks: [{ notebook_id: 'first', present: true }] } }], next_cursor: null })
    if (url === '/api/projects/p1') return reply({ id: 'p1', revision: 1, notebook_id: null, created_at: 1, data: { title: 'Markov', status: 'active', notebooks: [{ notebook_id: 'first', present: true }] } })
    if (url.startsWith('/api/roadmaps?project_id=p1')) return reply({ items: [MAIN, OTHER], next_cursor: null })
    if (url === '/api/notebooks/first/conversation') return reply({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Notebook conversation' } })
    if (url.startsWith('/api/conversations/canonical/messages')) return reply({ items: messages, next_cursor: null })
    if (url === '/api/agent/tasks/assign') return reply(coderRun('queued'), 201)
    if (url.startsWith('/api/agent/runs?conversation_id=canonical')) return reply({ items: runs })
    return reply({ items: [], next_cursor: null })
  }))
  return calls
}
const props = { cells: [{ id: 'c1', source: 'x = 1' }], revision: 5, showActivity: false, navigate: async () => true, onOpenNotebook: async () => true, onCommentCounts: () => {} }
const show = (extra: Record<string, unknown> = {}) => render(<Workspace {...props} {...extra} notebookId="first"
  renderNotebook={canvas => <>{canvas.beforeCells}{canvas.history}{canvas.composer}</>} />)
const assignButton = (title: string) => screen.getAllByRole('button', { name: 'Assign to Coding Agent' })
  .find(button => button.closest('li')?.textContent?.includes(title)) as HTMLButtonElement
const reason = (button: HTMLElement) => document.getElementById(button.getAttribute('aria-describedby') ?? '')?.textContent

it('shows Assign only for ready tasks of this notebook, explains why others are disabled, and posts exactly the task', async () => {
  const calls = backend()
  show()
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Assign to Coding Agent' })).toHaveLength(5))
  const ready = assignButton('Define a transition matrix')
  await waitFor(() => expect(ready.disabled).toBe(false))
  expect(ready.parentElement?.getAttribute('title')).toBe('Assign this task to the Coding Agent')
  for (const [title, text] of [['Simulate transitions', 'Waiting for: Define a transition matrix.'], ['Already going', 'This task is already in progress.'],
    ['Finished', 'This task is completed.'], ['Other notebook task', 'This roadmap belongs to another notebook; open that notebook to assign its tasks.']]) {
    const button = assignButton(title)
    expect(button.disabled).toBe(true)
    expect(reason(button)).toBe(text)
    expect(button.parentElement?.getAttribute('title')).toBe(text)
  }
  fireEvent.click(ready)
  await waitFor(() => expect(calls.filter(c => c.url === '/api/agent/tasks/assign')).toHaveLength(1))
  const body = calls.find(c => c.url === '/api/agent/tasks/assign')!.body!
  expect(Object.keys(body).sort()).toEqual(['operation_id', 'roadmap_id', 'task_id'])
  expect(body).toMatchObject({ roadmap_id: 'r1', task_id: 't1' })
})

it('disables Assign while a Coding Agent run is active, and shows no Assign button when agents are disabled', async () => {
  backend({ runs: [coderRun('running')] })
  const first = show()
  await waitFor(() => expect(reason(assignButton('Define a transition matrix'))).toBe('A Coding Agent run is already active in this notebook.'))
  expect(assignButton('Define a transition matrix').disabled).toBe(true)
  first.unmount()
  backend({ enabled: false })
  show()
  await screen.findAllByText('Define a transition matrix')
  await waitFor(() => expect(document.querySelectorAll('.task')).toHaveLength(5))
  expect(screen.queryByRole('button', { name: 'Assign to Coding Agent' })).toBeNull()
})

it('renders the system/tool assignment message, then its run panel with the pending approval card', async () => {
  const insert = approval('insert_code_cell', { title: 'Insert code cell', source: 'P = [[0.9, 0.1], [0.5, 0.5]]', explanation: 'Matrix', notebook_revision: 5 })
  backend({ messages: [message('assigned', 'system/tool', 'Owner assigned "Define a transition matrix" to the Coding Agent.')], runs: [coderRun('waiting_for_approval', [insert])] })
  show()
  const card = await screen.findByRole('region', { name: 'Approval: Insert code cell' })
  const article = document.getElementById('message-assigned')!
  expect(article.dataset.author).toBe('system/tool')
  expect(article.querySelector('p')?.textContent).toBe('Owner assigned "Define a transition matrix" to the Coding Agent.')
  expect(article.nextElementSibling?.classList.contains('agent-stream')).toBe(true)
  expect(article.nextElementSibling?.contains(card)).toBe(true)
  expect(within(card).getByText('P = [[0.9, 0.1], [0.5, 0.5]]').tagName).toBe('PRE')
  expect((within(card).getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
  expect((within(card).getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(false)
})

it('disables Approve on cell approvals while drafts exist or the revision differs; Deny and other cards stay enabled', async () => {
  const execute = approval('execute_cell', { title: 'Run code cell', source: 'print(1)', notebook_revision: 5, warning: 'Runs in this notebook kernel with your local permissions. Not a sandbox.' })
  const plan = approval('propose_roadmap', { title: 'Revise roadmap', objective: 'x', tasks: [{ title: 'A', depends_on: [], acceptance_criteria: ['x'] }], roadmap_revision: 4 })
  const delegate = approval('delegate_task', { title: "Assign 'A' to the Coding Agent", acceptance_criteria: ['x'], instructions: 'Do it', roadmap_revision: 4 })
  const guarded = (guard: { drafts: boolean; revision?: number }) => render(<>{[execute, plan, delegate].map(a => <ApprovalCard key={a.id} approval={a} guard={guard} />)}</>)
  const approveOf = (title: string) => within(screen.getByRole('region', { name: `Approval: ${title}` })).getByRole('button', { name: 'Approve' }) as HTMLButtonElement
  const denyOf = (title: string) => within(screen.getByRole('region', { name: `Approval: ${title}` })).getByRole('button', { name: 'Deny' }) as HTMLButtonElement

  const drafts = guarded({ drafts: true, revision: 5 })
  expect(approveOf('Run code cell').disabled).toBe(true)
  expect(approveOf('Run code cell').hasAttribute('disabled')).toBe(true)
  expect(reason(approveOf('Run code cell'))).toBe('Save or discard your edits first.')
  expect(denyOf('Run code cell').disabled).toBe(false)
  expect(screen.getByRole('note').textContent).toContain('Not a sandbox')
  for (const title of ['Revise roadmap', "Assign 'A' to the Coding Agent"]) {
    expect(approveOf(title).disabled).toBe(false)
    expect(approveOf(title).getAttribute('aria-describedby')).toBeNull()
  }
  drafts.unmount()
  const changed = guarded({ drafts: false, revision: 6 })
  expect(approveOf('Run code cell').disabled).toBe(true)
  expect(reason(approveOf('Run code cell'))).toBe('Notebook changed — the agent will re-propose.')
  changed.unmount()
  guarded({ drafts: false, revision: 5 })
  expect(approveOf('Run code cell').disabled).toBe(false)
  expect(approveOf('Run code cell').getAttribute('aria-describedby')).toBeNull()
})

it('Home and other cards without a guard are unaffected', () => {
  render(<ApprovalCard approval={approval('insert_code_cell', { title: 'Insert code cell', source: 'x', notebook_revision: 1 })} />)
  expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
})

it('the notebook view passes its drafts to approval cards', async () => {
  const insert = approval('insert_code_cell', { title: 'Insert code cell', source: 'x = 2', notebook_revision: 5 })
  backend({ messages: [message('assigned', 'system/tool', 'Owner assigned "Define a transition matrix" to the Coding Agent.')], runs: [coderRun('waiting_for_approval', [insert])] })
  show({ draftCells: ['c1'] })
  const card = await screen.findByRole('region', { name: 'Approval: Insert code cell' })
  const approve = within(card).getByRole('button', { name: 'Approve' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  expect(reason(approve)).toBe('Save or discard your edits first.')
  expect((within(card).getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(false)
})
