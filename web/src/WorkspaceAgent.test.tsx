// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Workspace } from './Workspace'

vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {}, settingsOpen: false, openSettings: () => {}, closeSettings: () => {} }),
}))

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

const props = { cells: [{ id: 'c1', source: 'x = 1' }], revision: 2, showActivity: false, navigate: async () => true, onOpenNotebook: async () => true, onCommentCounts: () => {} }
type Call = { url: string; method: string; body: Record<string, unknown> | null }

function backend({ enabled = true, start = [201] as number[] } = {}) {
  const calls: Call[] = [], messages: Record<string, unknown>[] = [], runs: Record<string, unknown>[] = []
  const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET', body = init?.body ? JSON.parse(init.body as string) : null
    calls.push({ url, method, body })
    if (url === '/api/agent/status') return reply({ enabled, provider: enabled ? 'demo' : null, model: null, reason: enabled ? null : 'No agent provider configured' })
    if (url === '/api/notebooks/first/conversation') return reply({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Notebook conversation' } })
    if (url.startsWith('/api/conversations/canonical/messages')) {
      if (method === 'POST') {
        const record = { id: `m${messages.length + 1}`, notebook_id: 'first', revision: 1, created_at: 1700000000,
          data: { author_role: 'user', content: body.content, parent_id: null, state: 'published', placement_after_cell_id: 'c1' } }
        messages.push(record); return reply(record, 201)
      }
      return reply({ items: messages, next_cursor: null })
    }
    if (url === '/api/agent/runs' && method === 'POST') {
      const status = start.shift() ?? 201
      if (status !== 201) return reply({ detail: 'Unavailable' }, status)
      const run = { id: 'r1', role: 'notebook_coordinator', state: 'waiting_for_approval', conversation_id: 'canonical', trigger_message_id: body.message_id,
        parent_run_id: null, notebook_id: 'first', project_id: null, provider: 'demo', model: 'deterministic-demo', error: null, result: null,
        created_at: 1, updated_at: 1, finished_at: null, tool_calls: [],
        approvals: [{ id: 'a1', tool: 'propose_roadmap', state: 'pending', digest: 'd'.repeat(64), expires_at: 1900000000,
          preview: { title: 'Create roadmap', objective: 'Understand the chain', tasks: [{ title: 'Define a transition matrix', depends_on: [], acceptance_criteria: ['Rows sum to 1'] }], roadmap_revision: null } }] }
      runs.push(run); return reply(run, 201)
    }
    if (url.startsWith('/api/agent/runs?conversation_id=canonical')) return reply({ items: runs })
    return reply({ items: [], next_cursor: null })
  }))
  return { calls, messages, runs }
}

const view = () => render(<Workspace {...props} notebookId="first" renderNotebook={canvas => <>{canvas.beforeCells}{canvas.afterCell('c1')}{canvas.history}{canvas.composer}</>} />)
const sendMessage = async (text: string) => {
  const box = await screen.findByRole('textbox', { name: 'Message' })
  await waitFor(() => expect(document.querySelector('[role="status"]')?.textContent ?? '').not.toMatch(/Loading/))
  fireEvent.change(box, { target: { value: text } })
  await waitFor(() => expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
}
const posts = (calls: Call[], url: string) => calls.filter(c => c.method === 'POST' && c.url === url)

it('renders nothing new when agents are disabled and only adds the status request', async () => {
  const { calls } = backend({ enabled: false })
  view()
  await sendMessage('What does this notebook do?')
  await waitFor(() => expect(posts(calls, '/api/conversations/canonical/messages')).toHaveLength(1))
  expect(await screen.findByText('What does this notebook do?')).toBeTruthy()
  expect(screen.queryByRole('checkbox', { name: 'Ask Coordinator' })).toBeNull()
  expect(document.querySelector('.agent-run, .agent-stream')).toBeNull()
  expect(calls.filter(c => c.url.startsWith('/api/agent'))).toEqual([{ url: '/api/agent/status', method: 'GET', body: null }])
})

it('posts the message first, then starts a run with exactly conversation, message and operation IDs, and shows its panel after the message', async () => {
  const { calls } = backend()
  view()
  const toggle = await screen.findByRole('checkbox', { name: 'Ask Coordinator' }) as HTMLInputElement
  expect(toggle.checked).toBe(true)
  expect(toggle.closest('.composer-shell')).toBeTruthy()
  expect(toggle.compareDocumentPosition(document.querySelector('.message-composer')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  await sendMessage('Plan how to understand this Markov chain.')
  await waitFor(() => expect(posts(calls, '/api/agent/runs')).toHaveLength(1))
  const message = posts(calls, '/api/conversations/canonical/messages')
  expect(message).toHaveLength(1)
  expect(message[0].body).toMatchObject({ content: 'Plan how to understand this Markov chain.' })
  expect(calls.indexOf(message[0])).toBeLessThan(calls.indexOf(posts(calls, '/api/agent/runs')[0]))
  const start = posts(calls, '/api/agent/runs')[0].body!
  expect(Object.keys(start).sort()).toEqual(['conversation_id', 'message_id', 'operation_id'])
  expect(start).toMatchObject({ conversation_id: 'canonical', message_id: 'm1' })
  const card = await screen.findByRole('region', { name: 'Approval: Create roadmap' })
  const article = document.getElementById('message-m1')!
  expect(article.dataset.author).toBe('user')
  expect(article.nextElementSibling?.classList.contains('agent-stream')).toBe(true)
  expect(article.nextElementSibling?.contains(card)).toBe(true)
  expect(localStorage.getItem('europa.agent.pending.run.canonical')).toBeNull()
})

it('sends only the message when Ask Coordinator is off and remembers the choice', async () => {
  const { calls } = backend()
  const first = view()
  const toggle = await screen.findByRole('checkbox', { name: 'Ask Coordinator' })
  fireEvent.click(toggle)
  expect(localStorage.getItem('europa.agent.ask')).toBe('0')
  await sendMessage('Just a note')
  await waitFor(() => expect(posts(calls, '/api/conversations/canonical/messages')).toHaveLength(1))
  await screen.findByText('Just a note')
  expect(posts(calls, '/api/agent/runs')).toHaveLength(0)
  first.unmount()
  view()
  expect((await screen.findByRole('checkbox', { name: 'Ask Coordinator' }) as HTMLInputElement).checked).toBe(false)
})

it('shows an unknown-outcome alert when starting fails with 503 and retries with the same operation without re-posting the message', async () => {
  const { calls } = backend({ start: [503, 201] })
  view()
  await screen.findByRole('checkbox', { name: 'Ask Coordinator' })
  await sendMessage('What is left?')
  const alert = await screen.findByText(/Starting the Coordinator has an unknown outcome\./)
  expect(alert.closest('[role="alert"]')).toBeTruthy()
  const firstStart = posts(calls, '/api/agent/runs')[0].body!
  expect(JSON.parse(localStorage.getItem('europa.agent.pending.run.canonical')!).body.operation_id).toBe(firstStart.operation_id)
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(posts(calls, '/api/agent/runs')).toHaveLength(2))
  expect(posts(calls, '/api/agent/runs')[1].body).toEqual(firstStart)
  await waitFor(() => expect(screen.queryByText(/Starting the Coordinator has an unknown outcome\./)).toBeNull())
  expect(posts(calls, '/api/conversations/canonical/messages')).toHaveLength(1)
  expect(localStorage.getItem('europa.agent.pending.run.canonical')).toBeNull()
})
