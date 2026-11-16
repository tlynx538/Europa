// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AgentRunPanel, ApprovalCard } from './AgentRun'
import { roadmapPreview, type ApprovalView, type RunView } from './agentApi'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

const revise: ApprovalView = {
  id: 'a2', tool: 'propose_roadmap', state: 'pending', digest: 'e'.repeat(64), expires_at: 1900000000,
  preview: {
    title: 'Revise roadmap', objective: 'Understand a <b>Markov</b> chain', rationale: 'Adds a comparison step.',
    tasks: [
      { title: 'Define a transition matrix', depends_on: [], acceptance_criteria: ['Rows sum to 1'], status: 'in_progress' },
      { title: 'Simulate transitions', depends_on: ['Define a transition matrix'], acceptance_criteria: ['A sequence is produced', 'Seeded'], status: 'pending' },
      { title: 'Compare with a 3-state chain', depends_on: ['Define a transition matrix', 'Simulate transitions'], acceptance_criteria: ['Compared'], status: 'pending' },
    ],
    removed_task_ids: ['old-task'], roadmap_revision: 1, injected_html: '<img src=x onerror=alert(1)>',
  },
}
const run = (over: Partial<RunView> = {}): RunView => ({
  id: 'r1', role: 'notebook_coordinator', state: 'completed', conversation_id: 'c', trigger_message_id: 'm', parent_run_id: null,
  notebook_id: 'n', project_id: 'p', provider: 'demo', model: 'deterministic-demo', error: null, result: null, created_at: 1,
  updated_at: 1, finished_at: 2, approvals: [], tool_calls: [], ...over,
})

it('renders a revision preview with ordered tasks, dependencies, criteria, removals and the bound revision, then posts the decision', async () => {
  const sent: { url: string; body: Record<string, unknown> }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    sent.push({ url, body: JSON.parse(init!.body as string) })
    return new Response(JSON.stringify({ id: 'a2', run_id: 'r1', state: 'approved', decided_at: 1 }))
  }))
  render(<ApprovalCard approval={revise} />)
  const card = screen.getByRole('region', { name: 'Approval: Revise roadmap' })
  expect(within(card).getByRole('heading').textContent).toBe('Revise roadmap')
  expect(card.textContent).toContain('Objective: Understand a <b>Markov</b> chain')
  expect(within(card).getByText('Adds a comparison step.')).toBeTruthy()
  const tasks = within(within(card).getByRole('list', { name: 'Proposed tasks' })).getAllByRole('listitem').filter(item => item.parentElement?.getAttribute('aria-label') === 'Proposed tasks')
  expect(tasks.map(item => item.querySelector('.agent-task-title')?.textContent)).toEqual(['Define a transition matrix', 'Simulate transitions', 'Compare with a 3-state chain'])
  expect(tasks[0].textContent).toContain('in progress')
  expect(tasks[1].textContent).toContain('after Define a transition matrix')
  expect(tasks[2].textContent).toContain('after Define a transition matrix, Simulate transitions')
  expect(Array.from(tasks[1].querySelectorAll('.agent-criteria li')).map(li => li.textContent)).toEqual(['A sequence is produced', 'Seeded'])
  expect(within(card).getByRole('note').textContent).toBe('Removes task: old-task')
  expect(within(card).getByText('Bound to roadmap revision 1')).toBeTruthy()
  expect(card.querySelector('img, b')).toBeNull()
  expect(card.textContent).not.toContain('onerror')
  fireEvent.click(within(card).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0].url).toBe('/api/agent/approvals/a2')
  expect(Object.keys(sent[0].body).sort()).toEqual(['decision', 'digest', 'operation_id'])
  expect(sent[0].body).toMatchObject({ decision: 'approve', digest: revise.digest })
})

it('renders a create preview without a revision binding and ignores malformed fields', () => {
  const create: ApprovalView = { ...revise, id: 'a3', preview: { title: 'Create roadmap', objective: 'Plan', roadmap_revision: null, removed_task_ids: [],
    tasks: [{ title: 'Inspect the data', depends_on: [], acceptance_criteria: ['Described'] }, 'not a task', { title: 7 }, { title: 'Summarize', depends_on: 'x', acceptance_criteria: [3, 'Done'] }] } }
  render(<ApprovalCard approval={create} />)
  const card = screen.getByRole('region', { name: 'Approval: Create roadmap' })
  expect(card.querySelectorAll('.agent-task-title').length).toBe(2)
  expect(card.textContent).not.toContain('Bound to roadmap revision')
  expect(within(card).queryByRole('note')).toBeNull()
  expect(roadmapPreview({ title: 'Create resources', actions: ['x'] })).toBeNull()
})

it('renders child runs after a collapsed completed parent so a child approval stays visible', () => {
  const child = run({ id: 'r2', role: 'coding_agent', state: 'waiting_for_approval', parent_run_id: 'r1',
    approvals: [{ ...revise, id: 'a9', tool: 'insert_code_cell', preview: { title: 'Insert code cell' } }] })
  const parent = run({ tool_calls: [{ id: 't1', name: 'read_roadmap', state: 'succeeded', summary: 'Read roadmap', status: 'ok', links: null, data: null }] })
  const { container } = render(<AgentRunPanel run={parent} children={[child]} />)
  const panels = container.querySelectorAll(':scope > details.agent-run')
  expect(panels).toHaveLength(2)
  expect((panels[0] as HTMLDetailsElement).open).toBe(false)
  expect(panels[0].querySelector('summary')?.textContent).toBe('Completed · 1 action')
  expect(panels[0].textContent).toContain('Read the roadmap')
  const card = screen.getByRole('region', { name: 'Approval: Insert code cell' })
  expect(panels[0].contains(card)).toBe(false)
  expect(card.closest('details')).toBe(panels[1])
  expect((panels[1] as HTMLDetailsElement).open).toBe(true)
})

it('offers Show cell for tool calls linked to a cell only when onRevealCell is given', () => {
  const linked = run({ tool_calls: [{ id: 't1', name: 'read_notebook', state: 'succeeded', summary: 'Read notebook', status: 'ok', links: { cell_id: 'cell-7' }, data: null }] })
  const reveal = vi.fn()
  const view = render(<AgentRunPanel run={linked} onRevealCell={reveal} />)
  fireEvent.click(screen.getByRole('button', { name: 'Show cell', hidden: true }))
  expect(reveal).toHaveBeenCalledWith('cell-7')
  view.rerender(<AgentRunPanel run={linked} />)
  expect(screen.queryByRole('button', { name: 'Show cell', hidden: true })).toBeNull()
})
