// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AgentRunPanel, ApprovalCard, pollDelay, useAgentRuns } from './AgentRun'
import { pendingKey, type ApprovalView, type RunView } from './agentApi'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

const approval: ApprovalView = {
  id: 'a1', tool: 'create_project_notebook', state: 'pending', digest: 'd'.repeat(64), expires_at: 1900000000,
  preview: { title: 'Create resources', purpose: 'Forecast revenue', actions: ['Create project folder Revenue Forecasting/', 'Create notebook Revenue Forecasting/exploratory-analysis.ipynb'] },
}
const run = (over: Partial<RunView> = {}): RunView => ({
  id: 'r1', role: 'home_coordinator', state: 'completed', conversation_id: 'c', trigger_message_id: 'm', notebook_id: null, project_id: null,
  provider: 'demo', model: 'deterministic-demo', error: null, result: null, created_at: 1, updated_at: 1, finished_at: 2,
  approvals: [], tool_calls: [], ...over,
})

it('shows the exact proposed actions and posts the digest-bound decision, clearing its pending key', async () => {
  const sent: { url: string; body: Record<string, unknown> }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    sent.push({ url, body: JSON.parse(init!.body as string) })
    return new Response(JSON.stringify({ id: 'a1', run_id: 'r1', state: 'approved', decided_at: 1 }))
  }))
  const decided = vi.fn()
  render(<ApprovalCard approval={approval} onDecided={decided} />)
  const card = screen.getByRole('region', { name: 'Approval: Create resources' })
  expect(within(card).getAllByRole('listitem').map(item => item.textContent)).toEqual(approval.preview.actions)
  expect(within(card).getByText('Forecast revenue')).toBeTruthy()
  fireEvent.click(within(card).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(decided).toHaveBeenCalledTimes(1))
  expect(sent).toHaveLength(1)
  expect(sent[0].url).toBe('/api/agent/approvals/a1')
  expect(Object.keys(sent[0].body).sort()).toEqual(['decision', 'digest', 'operation_id'])
  expect(sent[0].body).toMatchObject({ decision: 'approve', digest: approval.digest, operation_id: expect.any(String) })
  expect(localStorage.getItem(pendingKey('approval.a1'))).toBeNull()
})

it('keeps the pending decision after a 503 and retries with the same operation_id', async () => {
  const bodies: Record<string, unknown>[] = []
  let fail = true
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(init!.body as string))
    if (fail) return new Response(JSON.stringify({ detail: 'Busy' }), { status: 503 })
    return new Response(JSON.stringify({ id: 'a1', run_id: 'r1', state: 'approved', decided_at: 1 }))
  }))
  render(<ApprovalCard approval={approval} />)
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
  expect(await screen.findByText(/unknown outcome/)).toBeTruthy()
  const stored = JSON.parse(localStorage.getItem(pendingKey('approval.a1'))!)
  expect(stored.body.operation_id).toBe(bodies[0].operation_id)
  fail = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(bodies).toHaveLength(2))
  expect(bodies[1]).toEqual(bodies[0])
  await waitFor(() => expect(localStorage.getItem(pendingKey('approval.a1'))).toBeNull())
})

it('clears a pending decision on a definitive 4xx', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Approval is already approved' }), { status: 409 })))
  render(<ApprovalCard approval={approval} />)
  fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
  expect((await screen.findByRole('alert')).textContent).toMatch(/already approved/)
  expect(localStorage.getItem(pendingKey('approval.a1'))).toBeNull()
  expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy()
})

it('collapses a completed run to its action count with no Stop control', () => {
  const view = render(<AgentRunPanel run={run({ tool_calls: [{ id: 't', name: 'create_project_notebook', state: 'succeeded', summary: 'Created X/y.ipynb', status: 'ok', links: null, data: null }] })} />)
  const details = view.container.querySelector('details')!
  expect(details.open).toBe(false)
  expect(details.querySelector('summary')!.textContent).toBe('Completed · 1 action')
  expect(screen.queryByRole('button', { name: 'Stop agent' })).toBeNull()
})

it('opens an active run with a Stop control that explains its limits and sends an operation_id', async () => {
  const sent: { url: string; body: Record<string, unknown> }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { sent.push({ url, body: JSON.parse(init!.body as string) }); return new Response(JSON.stringify(run({ state: 'cancelling' }))) }))
  const view = render(<AgentRunPanel run={run({ state: 'running', finished_at: null })} />)
  expect(view.container.querySelector('details')!.open).toBe(true)
  expect(screen.getByText('Stopping ends future agent work only; it does not undo applied changes.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Stop agent' }))
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0]).toEqual({ url: '/api/agent/runs/r1/cancel', body: { operation_id: expect.any(String) } })
})

it('shows a pending approval card inside a waiting run', () => {
  const view = render(<AgentRunPanel run={run({ state: 'waiting_for_approval', finished_at: null, approvals: [approval] })} />)
  expect(view.container.querySelector('details')!.open).toBe(true)
  expect(screen.getByRole('region', { name: 'Approval: Create resources' })).toBeTruthy()
})

it('polls faster while active and reports only state changes', async () => {
  expect(pollDelay([run({ state: 'running' })])).toBe(800)
  expect(pollDelay([run({ state: 'waiting_for_approval' })])).toBe(2500)
  expect(pollDelay([run()])).toBe(4000)
  expect(pollDelay([])).toBe(4000)
  vi.useFakeTimers()
  const responses = [[run({ state: 'running' })], [run({ state: 'running', updated_at: 5 })], [run({ state: 'completed' })]]
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ items: responses[Math.min(fetcher.mock.calls.length - 1, 2)] })))
  vi.stubGlobal('fetch', fetcher)
  const changes = vi.fn()
  function Probe() { useAgentRuns('c', true, changes); return null }
  render(<Probe />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(changes).toHaveBeenCalledTimes(1)
  expect(changes.mock.calls[0][1]).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(800) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(changes).toHaveBeenCalledTimes(1)  // same states: no change notification
  await act(async () => { await vi.advanceTimersByTimeAsync(800) })
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(changes).toHaveBeenCalledTimes(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(3999) })
  expect(fetcher).toHaveBeenCalledTimes(3)  // terminal: slow 4 s cadence
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(fetcher).toHaveBeenCalledTimes(4)
  expect(String((fetcher.mock.calls[0] as unknown[])[0])).toBe('/api/agent/runs?conversation_id=c')
})
