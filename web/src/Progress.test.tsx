// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Workspace } from './Workspace'
import { ApprovalCard } from './AgentRun'
import type { ApprovalView } from './agentApi'

// Phase 4D Stage 3: live task progress in the Roadmap panel, completion attribution, Reopen,
// and the Re-propose action on stale cell proposals.
vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {}, settingsOpen: false, openSettings: () => {}, closeSettings: () => {} }),
}))
const scrolled = vi.fn()
beforeEach(() => { scrolled.mockReset(); Element.prototype.scrollIntoView = scrolled; HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') } })
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

const view = (task: Record<string, unknown>) => ({ parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1, links: {}, evidence: [], accepted: false,
  acceptance_criteria: ['Rows sum to 1'], progress: 0, display: { compact: false, checkmark: task.status === 'completed', show_progress: false }, ...task })
const roadmapOf = (tasks: Record<string, unknown>[], progress: number) => ({ id: 'r1', revision: 4, notebook_id: 'first', created_at: 1,
  data: { objective: 'Understand a Markov chain', progress, overall_display: { label: 'Overall progress', percentage_position: 'right' }, tasks: tasks.map(view) } })
const approval = (tool: string, preview: Record<string, unknown>): ApprovalView =>
  ({ id: `a-${tool}`, tool, state: 'pending', digest: 'd'.repeat(64), expires_at: 1900000000, preview } as ApprovalView)
const coder = (task: string, state: string, approvals: ApprovalView[] = []) => ({ id: `k-${task}`, role: 'coding_agent', state, conversation_id: 'canonical',
  trigger_message_id: 'assigned', parent_run_id: null, depth: 1, notebook_id: 'first', project_id: 'p1', provider: 'demo', model: 'm', error: null, result: null,
  created_at: 1, updated_at: 1, finished_at: null, task: { roadmap_id: 'r1', task_id: task, title: task, assigned_by: 'user' }, tool_calls: [], approvals })
const assigned = { id: 'assigned', notebook_id: 'first', revision: 1, created_at: 1700000000,
  data: { author_role: 'system/tool', content: 'Owner assigned "Define a transition matrix" to the Coding Agent.', parent_id: null, state: 'published', placement_after_cell_id: null } }

function backend(state: { tasks: Record<string, unknown>[]; progress: number; runs: unknown[] }) {
  const calls: { url: string; method: string; body: Record<string, unknown> | null }[] = []
  const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  const project = { id: 'p1', revision: 1, notebook_id: null, created_at: 1, data: { title: 'Markov', status: 'active', notebooks: [{ notebook_id: 'first', present: true }] } }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET', body = init?.body ? JSON.parse(init.body as string) : null
    calls.push({ url, method, body })
    if (url === '/api/agent/status') return reply({ enabled: true, provider: 'demo', model: null, reason: null })
    if (url.startsWith('/api/projects?')) return reply({ items: [project], next_cursor: null })
    if (url === '/api/projects/p1') return reply(project)
    if (url.startsWith('/api/roadmaps?project_id=p1')) return reply({ items: [roadmapOf(state.tasks, state.progress)], next_cursor: null })
    if (url === '/api/notebooks/first/conversation') return reply({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Notebook conversation' } })
    if (url.startsWith('/api/conversations/canonical/messages')) return reply({ items: [assigned], next_cursor: null })
    if (url.startsWith('/api/agent/runs?conversation_id=canonical')) return reply({ items: state.runs })
    if (url.startsWith('/api/agent/approvals/')) return reply({ id: 'a', run_id: 'k', state: 'denied', decided_at: 2 })
    return reply({ items: [], next_cursor: null })
  }))
  return calls
}
const show = (revision = 5) => render(<Workspace cells={[{ id: 'c1', source: 'x = 1' }]} revision={revision} showActivity={false} navigate={async () => true}
  onOpenNotebook={async () => true} onCommentCounts={() => {}} notebookId="first" renderNotebook={canvas => <>{canvas.beforeCells}{canvas.history}{canvas.composer}</>} />)
const taskItem = (title: string) => Array.from(document.querySelectorAll<HTMLElement>('li.task')).find(li => li.querySelector(':scope > button')?.textContent?.endsWith(title))!

it('shows each agent state on its task and jumps to the pending approval card', async () => {
  const insert = approval('insert_code_cell', { title: 'Insert code cell', source: 'P = 1', notebook_revision: 5 })
  backend({ progress: 0, tasks: [{ id: 't1', title: 'Define a transition matrix', status: 'in_progress' },
    { id: 't2', title: 'Simulate transitions', status: 'in_progress' }, { id: 't3', title: 'Plot', status: 'blocked', explanation: 'Coding Agent: run failed: Budget exhausted: model requests' }],
    runs: [coder('t1', 'waiting_for_approval', [insert]), coder('t2', 'running')] })
  show()
  await screen.findByRole('region', { name: 'Approval: Insert code cell' })
  await waitFor(() => expect(taskItem('Define a transition matrix').querySelector('.agent-task-status')?.textContent).toBe('Waiting for your approval Show approval'))
  expect(taskItem('Simulate transitions').querySelector('.agent-task-status')?.textContent).toBe('Coding Agent working · running')
  expect(taskItem('Plot').querySelector('.agent-task-status')?.textContent).toBe('Blocked: Coding Agent: run failed: Budget exhausted: model requests')
  fireEvent.click(within(taskItem('Define a transition matrix')).getByRole('button', { name: 'Show approval' }))
  const card = screen.getByRole('region', { name: 'Approval: Insert code cell' })
  expect(scrolled.mock.contexts.at(-1)).toBe(card)
  expect(document.activeElement).toBe(within(card).getByRole('button', { name: 'Approve' }))
})

it('marks agent completions with the verifying receipt or notes, and Reopen prefills in_progress', async () => {
  backend({ progress: 66.67, runs: [], tasks: [
    { id: 't1', title: 'Define a transition matrix', status: 'completed', accepted: true, accepted_by: 'coding_agent', evidence: [{ summary: 'verified', execution_receipt_id: 'abcdef1234567890abcdef1234567890' }] },
    { id: 't2', title: 'Write notes', status: 'completed', accepted: true, accepted_by: 'coding_agent', evidence: [{ summary: 'notes', cell_ids: ['note-cell-1234', 'note-cell-5678'] }] },
    { id: 't3', title: 'Owner task', status: 'completed', accepted: true, accepted_by: 'user', evidence: [{ summary: 'checked' }] }] })
  show()
  await waitFor(() => expect(taskItem('Define a transition matrix')).toBeTruthy())
  const title = (name: string) => taskItem(name).querySelector(':scope > button')!
  expect(title('Define a transition matrix').textContent).toBe('✓ Define a transition matrix')
  expect(title('Define a transition matrix').getAttribute('title')).toBe('Completed by Coding Agent — verified by receipt abcdef12')
  expect(title('Write notes').getAttribute('title')).toBe('Completed by Coding Agent — verified by notes note-cel, note-cel')
  expect(title('Owner task').getAttribute('title')).toBeNull()
  fireEvent.click(within(taskItem('Define a transition matrix')).getByRole('button', { name: 'Reopen' }))
  const editor = await screen.findByRole('dialog')
  expect((within(editor).getByRole('combobox', { name: 'Status' }) as HTMLSelectElement).value).toBe('in_progress')
  expect((within(editor).getByRole('textbox', { name: 'Title' }) as HTMLInputElement).value).toBe('Define a transition matrix')
})

it('updates overall progress when a run finishes, without a reload', async () => {
  const state = { progress: 0, tasks: [{ id: 't1', title: 'Define a transition matrix', status: 'in_progress' }, { id: 't2', title: 'Simulate', dependencies: ['t1'] },
    { id: 't3', title: 'Stationary' }, { id: 't4', title: 'Plot' }] as Record<string, unknown>[], runs: [coder('t1', 'running')] as unknown[] }
  backend(state)
  show()
  await waitFor(() => expect(document.querySelector('.roadmap-title strong')?.textContent).toBe('0%'))
  await waitFor(() => expect(taskItem('Define a transition matrix').querySelector('.agent-task-status')?.textContent).toBe('Coding Agent working · running'))
  state.tasks = [{ ...state.tasks[0], status: 'completed', accepted: true, accepted_by: 'coding_agent', evidence: [{ summary: 'ok', execution_receipt_id: 'f'.repeat(32) }] }, ...state.tasks.slice(1)]
  state.progress = 25
  state.runs = [coder('t1', 'completed')]
  await waitFor(() => expect(document.querySelector('.roadmap-title strong')?.textContent).toBe('25%'), { timeout: 4000 })
  expect(taskItem('Define a transition matrix').querySelector('.agent-task-status')).toBeNull()
  expect((document.querySelector('progress[aria-label="Overall project progress"]') as HTMLProgressElement).value).toBe(25)
}, 10000)

it('a stale cell card offers Re-propose and Deny with Approve disabled, and sends the repropose decision', async () => {
  const calls = backend({ progress: 0, tasks: [], runs: [] })
  const stale = approval('insert_code_cell', { title: 'Insert code cell', source: 'P = 1', notebook_revision: 5 })
  render(<ApprovalCard approval={stale} guard={{ drafts: false, revision: 6 }} />)
  const approve = screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  expect(document.getElementById(approve.getAttribute('aria-describedby')!)?.textContent).toBe('Notebook changed since this was proposed.')
  expect((screen.getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Re-propose' }))
  await waitFor(() => expect(calls.filter(c => c.url.startsWith('/api/agent/approvals/'))).toHaveLength(1))
  expect(calls.find(c => c.url.startsWith('/api/agent/approvals/'))!.body).toMatchObject({ decision: 'repropose', digest: 'd'.repeat(64) })
  cleanup()
  render(<ApprovalCard approval={stale} guard={{ drafts: true, revision: 6 }} />)  // drafts first: no Re-propose
  expect(screen.queryByRole('button', { name: 'Re-propose' })).toBeNull()
  expect(screen.getByText('Save or discard your edits first.')).toBeTruthy()
})
