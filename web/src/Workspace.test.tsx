// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
