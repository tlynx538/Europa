// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { Workspace } from './Workspace'
import { consumeKickoffFocus, kickoffFocusPending, openQuestion, requestKickoffFocus } from './kickoffFocus'
import type { Message, RecordData } from './applicationApi'

// Phase 4D Stage 1: the kickoff questionnaire in the notebook view.
vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {}, settingsOpen: false, openSettings: () => {}, closeSettings: () => {} }),
}))

const scrolled = vi.fn()
beforeEach(() => { scrolled.mockReset(); Element.prototype.scrollIntoView = scrolled })
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); consumeKickoffFocus(); document.querySelectorAll('.cm-editor').forEach(n => n.remove()) })

const QUESTIONS = 'Before we start on the Markov chain, a few questions:\n\n1. What do you want to achieve with the Markov chain?\n2. What data should the Markov chain work use?\n3. What outputs do you expect?'
const record = (id: string, author: string, content: string, state = 'published') =>
  ({ id, notebook_id: 'first', revision: 1, created_at: 1700000000, data: { author_role: author, content, parent_id: null, state, placement_after_cell_id: null } })
const origin = record('origin', 'coordinator', 'Notebook created from a Home conversation. Purpose: Markov chain')
const asked = record('asked', 'coordinator', QUESTIONS, 'needs_input')
const kickoffRun = (state: string, withCard: boolean) => ({
  id: 'k1', role: 'notebook_coordinator', state, conversation_id: 'canonical', trigger_message_id: 'origin', parent_run_id: null, notebook_id: 'first',
  project_id: 'p1', provider: 'demo', model: 'deterministic-demo', error: null, result: null, created_at: 1, updated_at: 1, finished_at: null, task: { kickoff: true },
  tool_calls: withCard ? [{ id: 't1', name: 'ask_owner', state: 'succeeded', summary: 'Asked the owner 3 questions', status: 'ok', links: null, data: null },
    { id: 't2', name: 'propose_roadmap', state: 'awaiting_approval', summary: null, status: null, links: null, data: null }] : [],
  approvals: withCard ? [{ id: 'a1', tool: 'propose_roadmap', state: 'pending', digest: 'd'.repeat(64), expires_at: 1900000000,
    preview: { title: 'Create roadmap', objective: 'Understand a simple Markov chain', rationale: 'Draft built from the purpose; answers to the questions can refine it.',
      tasks: [{ title: 'Define a transition matrix', depends_on: [], acceptance_criteria: ['Rows sum to 1'] }], roadmap_revision: null } }] : [] })

function backend({ enabled = true, messages = [origin, asked] as ReturnType<typeof record>[], runs = [kickoffRun('waiting_for_approval', true)] as unknown[] } = {}) {
  const state = { messages, runs }
  const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/agent/status') return reply({ enabled, provider: enabled ? 'demo' : null, model: null, reason: enabled ? null : 'No agent provider configured' })
    if (url === '/api/notebooks/first/conversation') return reply({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Notebook conversation' } })
    if (url.startsWith('/api/conversations/canonical/messages')) return reply({ items: state.messages, next_cursor: null })
    if (url.startsWith('/api/agent/runs?conversation_id=canonical')) return reply({ items: state.runs })
    return reply({ items: [], next_cursor: null })
  }))
  return state
}

const props = { cells: [] as { id: string; source?: string }[], revision: 1, showActivity: false, navigate: async () => true, onOpenNotebook: async () => true, onCommentCounts: () => {} }
const view = () => render(<Workspace {...props} notebookId="first" renderNotebook={canvas => <>{canvas.beforeCells}{canvas.history}{canvas.composer}</>} />)
const composer = () => screen.findByRole('textbox', { name: 'Message' }) as Promise<HTMLTextAreaElement>

it('marks Coordinator questions with a Needs your input badge, keeps user rendering, and shows the kickoff card after the questions', async () => {
  backend({ messages: [origin, asked, record('mine', 'user', 'A note to self', 'needs_input')], runs: [kickoffRun('waiting_for_approval', true)] })
  view()
  const card = await screen.findByRole('region', { name: 'Approval: Create roadmap' })
  const question = document.getElementById('message-asked')!
  expect(question.dataset.author).toBe('coordinator')
  expect(question.querySelector('.needs-input-badge')?.textContent).toBe('Needs your input')
  expect(question.querySelector('strong')?.textContent).toBe('coordinator')
  expect(question.querySelector('p')?.textContent).toBe(QUESTIONS)
  // The kickoff run (triggered by the coordinator-authored origin message) renders after the questions.
  expect(question.nextElementSibling?.classList.contains('agent-stream')).toBe(true)
  expect(question.nextElementSibling?.contains(card)).toBe(true)
  expect(document.getElementById('message-origin')!.nextElementSibling?.id).toBe('message-asked')
  expect(screen.getByText('Ask the owner')).toBeTruthy()
  // Owner messages keep the existing plain text marker and no badge.
  const mine = document.getElementById('message-mine')!
  expect(mine.querySelector('strong')?.textContent).toBe('user · Needs input')
  expect(mine.querySelector('.needs-input-badge')).toBeNull()
  expect(document.getElementById('message-origin')!.querySelector('.needs-input-badge')).toBeNull()
})

it('uses the answer placeholder only while the newest question is unanswered', async () => {
  const state = backend()
  const first = view()
  await waitFor(async () => expect((await composer()).placeholder).toBe("Answer the Coordinator's questions…"))
  first.unmount()
  state.messages = [origin, asked, record('answer', 'user', 'Simulated weather data.')]
  view()
  await screen.findByText('Simulated weather data.')
  expect((await composer()).placeholder).toBe('Write a message...')
})

it('keeps the default placeholder when agents are disabled', async () => {
  backend({ enabled: false, runs: [] })
  view()
  await screen.findByText(QUESTIONS.split('\n')[0], { exact: false })
  expect((await composer()).placeholder).toBe('Write a message...')
})

it('after Open notebook, scrolls to the questions once they arrive and focuses the composer, exactly once', async () => {
  const state = backend({ messages: [origin], runs: [kickoffRun('running', false)] })
  requestKickoffFocus('first')
  const first = view()
  const box = await composer()
  await screen.findByText(origin.data.content)
  expect(scrolled).not.toHaveBeenCalled()
  expect(kickoffFocusPending('first')).toBe(true)
  // The kickoff run posts its questions; the run poll reloads the messages.
  state.messages = [origin, asked]
  state.runs = [kickoffRun('waiting_for_approval', true)]
  await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1), { timeout: 4000 })
  expect(scrolled.mock.contexts[0]).toBe(document.getElementById('message-asked'))
  await waitFor(() => expect(document.activeElement).toBe(box))
  expect(kickoffFocusPending('first')).toBe(false)
  // One-shot: a later visit does not scroll or take focus again.
  first.unmount()
  view()
  await screen.findByRole('region', { name: 'Approval: Create roadmap' })
  await composer()
  expect(scrolled).toHaveBeenCalledTimes(1)
  expect(document.activeElement).toBe(document.body)
}, 10000)

it('does not steal focus from a cell editor', async () => {
  backend()
  const editor = document.createElement('div'); editor.className = 'cm-editor'
  const content = document.createElement('div'); content.tabIndex = 0; content.setAttribute('contenteditable', 'true')
  editor.appendChild(content); document.body.appendChild(editor); content.focus()
  expect(document.activeElement).toBe(content)
  requestKickoffFocus('first')
  view()
  await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1))
  await new Promise(resolve => requestAnimationFrame(resolve))
  expect(document.activeElement).toBe(content)
  expect(kickoffFocusPending('first')).toBe(false)
})

it('ignores a focus request for another notebook', async () => {
  backend()
  requestKickoffFocus('other')
  view()
  await screen.findByRole('region', { name: 'Approval: Create roadmap' })
  expect(scrolled).not.toHaveBeenCalled()
  expect(kickoffFocusPending('other')).toBe(false)
})

it('finds the open question only when no owner message follows it', () => {
  const list = (items: ReturnType<typeof record>[]) => items as unknown as RecordData<Message>[]
  expect(openQuestion(list([origin, asked]))?.id).toBe('asked')
  expect(openQuestion(list([origin, asked, record('reply', 'coordinator', 'Saved the draft roadmap.')]))?.id).toBe('asked')
  expect(openQuestion(list([origin, asked, record('answer', 'user', 'Yes')]))).toBeNull()
  expect(openQuestion(list([origin]))).toBeNull()
})
