// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { Workspace } from './Workspace'

vi.mock('./Layout', () => ({
  HeaderSlot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  usePanels: () => ({ mobile: false, setLeft: () => {}, setRight: () => {} }),
}))

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

const project = { id: 'p1', revision: 1, data: { title: 'Research', status: 'active', notebooks: [{ notebook_id: 'first', present: true }] } }
const props = { cells: [], showActivity: false, onOpenNotebook: async () => true, onCommentCounts: () => {} }

it('explains a lost project context after switching to an unrelated notebook', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url === '/api/projects/p1' ? project : { items: url.startsWith('/api/projects?') ? [project] : [], next_cursor: null },
  ))))
  const view = render(<Workspace {...props} notebookId="first" />)
  await screen.findByRole('option', { name: 'Research' })
  await waitFor(() => expect((screen.getByRole('combobox', { name: 'Project' }) as HTMLSelectElement).value).toBe('p1'), { timeout: 5000 })
  expect(screen.queryByText(/Project context cleared/)).toBeNull()
  view.rerender(<Workspace {...props} notebookId="other" />)
  expect(await screen.findByText(/Project context cleared: Research is not linked to this notebook/)).toBeTruthy()
})

it('does not warn when a notebook had no previous project context', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }))))
  render(<Workspace {...props} notebookId="other" />)
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Project' })).toBeTruthy())
  expect(screen.queryByText(/Project context cleared/)).toBeNull()
})
