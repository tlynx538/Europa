// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { Workspace } from './Workspace'

let settingsOpen = false
vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {}, settingsOpen, openSettings: () => {}, closeSettings: () => {} }),
}))

afterEach(() => { cleanup(); settingsOpen = false; localStorage.clear(); vi.unstubAllGlobals() })

const project = { id: 'p1', revision: 1, data: { title: 'Research', status: 'active', notebooks: [{ notebook_id: 'first', present: true }] } }
const props = { cells: [], showActivity: false, navigate: async () => true, onOpenNotebook: async () => true, onCommentCounts: () => {} }

it('explains a lost project context after switching to an unrelated notebook', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url === '/api/projects/p1' ? project : { items: url.startsWith('/api/projects?') ? [project] : [], next_cursor: null },
  ))))
  const view = render(<Workspace {...props} notebookId="first" />)
  await waitFor(() => expect((document.querySelector('.project-settings-dialog select') as HTMLSelectElement).value).toBe('p1'), { timeout: 5000 })
  expect(screen.queryByText(/Project context cleared/)).toBeNull()
  view.rerender(<Workspace {...props} notebookId="other" />)
  expect(await screen.findByText(/Project context cleared: Research is not linked to this notebook/)).toBeTruthy()
})

it('does not warn when a notebook had no previous project context', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }))))
  render(<Workspace {...props} notebookId="other" />)
  await waitFor(() => expect(document.querySelector('.project-settings-dialog select')).toBeTruthy())
  expect(screen.queryByText(/Project context cleared/)).toBeNull()
})

it('shows selected project membership in the settings dialog instead of a roadmap disclosure', async () => {
  settingsOpen = true
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url === '/api/projects/p1' ? project : { items: url.startsWith('/api/projects?') ? [project] : [], next_cursor: null },
  ))))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  render(<Workspace {...props} notebookId="first" />)
  const dialog = await screen.findByRole('dialog', { name: 'Project settings' })
  await waitFor(() => expect(dialog.textContent).toContain('1 linked notebook'))
  expect(dialog.textContent).toContain('Research')
  expect(dialog.textContent).toContain('1 linked notebook')
  expect(dialog.textContent).toContain('Current notebook is linked')
  expect(screen.queryByText('Project settings', { selector: 'summary' })).toBeNull()
})

it('loads the canonical notebook conversation without a project, retains its id across project context changes and exposes no new-thread control', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(url)
    if (url === '/api/notebooks/first/conversation') return new Response(JSON.stringify({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Notebook conversation' } }))
    if (url === '/api/projects/p1') return new Response(JSON.stringify(project))
    if (url.includes('/conversations/canonical/messages?')) return new Response(JSON.stringify({ items: [{ id: 'message', notebook_id: 'first', created_at: 1700000000, data: { author_role: 'user', content: 'saved', parent_id: null, state: 'published', placement_after_cell_id: null } }], next_cursor: null }))
    return new Response(JSON.stringify({ items: url.startsWith('/api/projects?') ? [project] : [], next_cursor: null }))
  }))
  const view = render(<Workspace {...props} notebookId="first" renderNotebook={canvas => <><div>{canvas.beforeCells}{canvas.history}</div>{canvas.composer}</>} />)
  expect(await screen.findByText('saved')).toBeTruthy()
  const timestamp = document.querySelector<HTMLTimeElement>('.message-meta time')!
  expect(timestamp.textContent).toBe(new Date(1700000000 * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))
  expect(timestamp.title).toBe(new Date(1700000000 * 1000).toLocaleString())
  expect(document.querySelector('.stream-messages')?.textContent).not.toContain('Legacy message')
  expect(screen.getByRole('textbox', { name: 'Message' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /New conversation|Start discussion/ })).toBeNull()
  expect(calls).toContain('/api/notebooks/first/conversation')
  view.rerender(<Workspace {...props} notebookId="first" renderNotebook={canvas => <><div>{canvas.beforeCells}{canvas.history}</div>{canvas.composer}</>} />)
  expect(screen.getByText('saved')).toBeTruthy()
  expect(calls.filter(c => c === '/api/notebooks/first/conversation')).toHaveLength(1)
})

it('keeps an always-visible retry shell on a canonical GET error', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url === '/api/notebooks/other/conversation' ? new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 503 }) : new Response(JSON.stringify({ items: [], next_cursor: null }))))
  render(<Workspace {...props} notebookId="other" renderNotebook={canvas => <>{canvas.history}{canvas.composer}</>} />)
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Retry conversation' }).length).toBeGreaterThan(0))
  expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
})

it('keeps a recovered message retry disabled until its canonical scope is loaded', async () => {
  localStorage.setItem('europa.pending.application', JSON.stringify({ path: '/conversations/canonical/messages', method: 'POST', body: { notebook_id: 'first', content: 'Retained', operation_id: 'same' } }))
  let release!: (response: Response) => void
  const canonical = new Promise<Response>(resolve => { release = resolve })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url === '/api/notebooks/first/conversation' ? canonical : new Response(JSON.stringify({ items: [], next_cursor: null }))))
  render(<Workspace {...props} notebookId="first" renderNotebook={canvas => <>{canvas.history}{canvas.composer}</>} />)
  expect(screen.getAllByRole<HTMLButtonElement>('button', { name: 'Retry pending operation' }).every(button => button.disabled)).toBe(true)
  await act(async () => release(new Response(JSON.stringify({ id: 'canonical', notebook_id: 'first', revision: 1, data: { title: 'Conversation' } }))))
  await waitFor(() => expect(screen.getAllByRole<HTMLButtonElement>('button', { name: 'Retry pending operation' }).every(button => !button.disabled)).toBe(true))
  expect(JSON.parse(localStorage.getItem('europa.pending.application')!).body.operation_id).toBe('same')
})
