// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HomeCoordinator, homeDraftKey } from './HomeCoordinator'
import { pendingKey } from './agentApi'
import { consumeKickoffFocus, kickoffFocusPending } from './kickoffFocus'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const C = 'c'.repeat(32), M = 'm'.repeat(32), N = 'n'.repeat(32)
const enabled = { enabled: true, provider: 'demo', model: 'deterministic-demo', reason: null }
const message = (id: string, author: string, content: string, extra = {}) =>
  ({ id, revision: 1, notebook_id: null, created_at: 1, data: { content, author_role: author, parent_id: null, state: 'published', ...extra } })

type Call = { method: string; url: string; body?: Record<string, unknown> }
function server(options: { runStatus?: () => number; runs?: () => unknown[] } = {}) {
  const calls: Call[] = []
  const messages: ReturnType<typeof message>[] = []
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET', body = init?.body ? JSON.parse(init.body as string) : undefined
    calls.push({ method, url, body })
    const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
    if (url === '/api/agent/status') return reply(enabled)
    if (url === '/api/agent/home/conversation') return reply({ id: C, revision: 1, notebook_id: null, created_at: 1, data: { title: 'Home' } })
    if (url.startsWith(`/api/conversations/${C}/messages`) && method === 'POST') { const posted = message(M, 'user', body.content); messages.push(posted); return reply(posted, 201) }
    if (url.startsWith(`/api/conversations/${C}/messages`)) return reply({ items: messages, next_cursor: null })
    if (url === '/api/agent/runs' && method === 'POST') { const status = options.runStatus?.() ?? 201; return reply(status >= 400 ? { detail: 'Unavailable' } : { id: 'r1', state: 'queued' }, status) }
    if (url.startsWith('/api/agent/runs?')) return reply({ items: options.runs?.() ?? [] })
    return reply({ detail: 'unexpected' }, 404)
  })
  vi.stubGlobal('fetch', fetcher)
  return { calls, messages, fetcher }
}

it('renders nothing and makes only the status request when agents are disabled', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ enabled: false, provider: null, model: null, reason: 'No agent provider configured' })))
  vi.stubGlobal('fetch', fetcher)
  const view = render(<HomeCoordinator navigate={async () => true} />)
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  await new Promise(resolve => setTimeout(resolve, 50))
  expect(view.container.innerHTML).toBe('')
  expect(fetcher.mock.calls.map(call => String((call as unknown[])[0]))).toEqual(['/api/agent/status'])
})

it('renders nothing when status is unavailable or malformed', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('null')))
  const view = render(<HomeCoordinator navigate={async () => true} />)
  await new Promise(resolve => setTimeout(resolve, 50))
  expect(view.container.innerHTML).toBe('')
})

it('posts the owner message, then starts a run with exactly conversation, message and operation IDs', async () => {
  const { calls } = server()
  render(<HomeCoordinator navigate={async () => true} />)
  expect(await screen.findByText('Deterministic demo provider (not an LLM)')).toBeTruthy()
  const box = await screen.findByRole('textbox', { name: 'Message the Coordinator' })
  await waitFor(() => expect((box as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(box, { target: { value: 'I want to understand a simple Markov chain.' } })
  expect(localStorage.getItem(homeDraftKey(C))).toBe('I want to understand a simple Markov chain.')
  fireEvent.keyDown(box, { key: 'Enter' })
  await waitFor(() => expect(calls.some(call => call.method === 'POST' && call.url === '/api/agent/runs')).toBe(true))
  const posts = calls.filter(call => call.method === 'POST')
  expect(posts.map(call => call.url)).toEqual([`/api/conversations/${C}/messages`, '/api/agent/runs'])
  expect(posts[0].body).toEqual({ content: 'I want to understand a simple Markov chain.', operation_id: expect.any(String) })
  expect(Object.keys(posts[1].body!).sort()).toEqual(['conversation_id', 'message_id', 'operation_id'])
  expect(posts[1].body).toEqual({ conversation_id: C, message_id: M, operation_id: expect.any(String) })
  expect(localStorage.getItem(homeDraftKey(C))).toBeNull()
  expect(localStorage.getItem(pendingKey(`run.${C}`))).toBeNull()
  expect(await screen.findByText('I want to understand a simple Markov chain.', { selector: '.home-thread [data-author="user"] p' })).toBeTruthy()
})

it('keeps Shift+Enter as a newline', async () => {
  const { calls } = server()
  render(<HomeCoordinator navigate={async () => true} />)
  const box = await screen.findByRole('textbox', { name: 'Message the Coordinator' })
  await waitFor(() => expect((box as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(box, { target: { value: 'line one' } })
  fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
  await new Promise(resolve => setTimeout(resolve, 30))
  expect(calls.filter(call => call.method === 'POST')).toEqual([])
})

it('reports an unknown run-start outcome and retries with the stored operation_id', async () => {
  let status = 503
  const { calls } = server({ runStatus: () => status })
  render(<HomeCoordinator navigate={async () => true} />)
  const box = await screen.findByRole('textbox', { name: 'Message the Coordinator' })
  await waitFor(() => expect((box as HTMLTextAreaElement).disabled).toBe(false))
  fireEvent.change(box, { target: { value: 'Forecast revenue' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText(/Starting the Coordinator has an unknown outcome/)).toBeTruthy()
  status = 201
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(calls.filter(call => call.url === '/api/agent/runs')).toHaveLength(2))
  const [first, second] = calls.filter(call => call.url === '/api/agent/runs')
  expect(second.body).toEqual(first.body)
  expect(calls.filter(call => call.method === 'POST' && call.url.includes('/messages'))).toHaveLength(1)
  await waitFor(() => expect(screen.queryByText(/unknown outcome/)).toBeNull())
})

it('offers Open notebook on coordinator replies and refreshes the workspace on a newly succeeded creation', async () => {
  const navigate = vi.fn(async () => true), changed = vi.fn()
  let created = false
  const call = () => ({ id: 't1', name: 'create_project_notebook', state: created ? 'succeeded' : 'awaiting_approval', summary: null, status: null, links: null, data: null })
  const { messages } = server({ runs: () => [{ id: 'r1', role: 'home_coordinator', state: created ? 'completed' : 'waiting_for_approval', conversation_id: C, trigger_message_id: M,
    notebook_id: null, project_id: null, provider: 'demo', model: 'm', error: null, result: null, created_at: 1, updated_at: 1, finished_at: null, approvals: [], tool_calls: [call()] }] })
  messages.push(message(M, 'user', 'I want to understand a simple Markov chain.'))
  render(<HomeCoordinator navigate={navigate} onWorkspaceChanged={changed} />)
  expect(await screen.findByText('Waiting for your approval')).toBeTruthy()
  expect(changed).not.toHaveBeenCalled()
  created = true
  messages.push(message('r'.repeat(32), 'coordinator', 'Created Markov Chain/markov-chain.ipynb.', { notebook_id: N }))
  fireEvent.click(await screen.findByRole('button', { name: 'Open notebook' }, { timeout: 4000 }))
  expect(navigate).toHaveBeenCalledWith(`/notebooks/${N}`)
  expect(kickoffFocusPending(N)).toBe(true)  // Phase 4D: the notebook view reveals the kickoff questions once
  consumeKickoffFocus()
  expect(changed).toHaveBeenCalledTimes(1)
  expect(document.querySelector('.home-thread [data-author="coordinator"]')?.textContent).toContain('Created Markov Chain/markov-chain.ipynb')
}, 10000)
