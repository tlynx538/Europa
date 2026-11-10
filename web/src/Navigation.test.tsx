// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Navigation } from './Navigation'

afterEach(() => { cleanup(); localStorage.clear(); history.replaceState(null, '', '/'); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('offers root Browse without replacing ordinary folders with unverified projects', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/workspace?') ? { path: '', items: [{ name: 'Research', type: 'directory', size: null, modified: 0 }], next_cursor: null } : url.includes('/recent') ? { items: [] } : url.includes('/projects?') ? { items: [], next_cursor: null } : [])))
  render(<Navigation openNotebook={async () => true} renderNotebook={() => null} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Research' }))
  await waitFor(() => expect(location.pathname + location.search).toBe('/browse?path=Research'))
  expect(screen.getByRole('navigation', { name: 'Current directory' }).textContent).toContain('Research')
  expect(screen.queryByRole('link', { name: 'Projects' })).toBeNull()
})

it('keeps the verified project origin for an immediate notebook close', async () => {
  history.replaceState(null, '', '/projects/p')
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/p') ? { id: 'p', revision: 1, data: { title: 'Research', status: 'active', notebooks: [{ notebook_id: 'n', present: true }] } } : url.endsWith('/notebooks') ? [{ notebook_id: 'n', title: 'Notebook', revision: 1 }] : { items: [], next_cursor: null })))
  render(<Navigation openNotebook={async () => true} renderNotebook={(_, navigate, closeTarget) => <button onClick={() => void navigate(closeTarget ?? '/')}>Close Notebook</button>} />)
  fireEvent.click(await screen.findByRole('link', { name: 'Notebook' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Close Notebook' }))
  await waitFor(() => expect(location.pathname).toBe('/projects/p'))
})

it('guards Europa Home from a text editor with an unresolved save', async () => {
  history.replaceState(null, '', '/text?path=notes.txt')
  localStorage.setItem('europa.workspace.pending', JSON.stringify({ path: 'notes.txt', content: 'mine', revision: 'a'.repeat(64), operation_id: 'op' }))
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ path: 'notes.txt', content: 'saved', revision: 'a'.repeat(64) })))
  render(<Navigation openNotebook={async () => true} renderNotebook={() => null} />)
  await screen.findByRole('textbox', { name: 'File contents' })
  fireEvent.click(screen.getByRole('link', { name: 'Europa' }))
  expect(location.pathname).toBe('/text')
  expect(screen.getByRole('alert').textContent).toMatch(/unknown outcome/)
})

it('opens the Files dashboard at root and preserves directory browser history', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ path: '', items: [{ name: 'Research', type: 'directory', size: null, modified: 0 }], next_cursor: null })))
  render(<Navigation openNotebook={async () => true} renderNotebook={() => <p>Editor</p>} />)
  expect(location.pathname).toBe('/')
  expect(await screen.findByRole('tab', { name: 'Files' })).toBeTruthy()
  fireEvent.click(await screen.findByRole('button', { name: 'Research' }))
  await waitFor(() => expect(location.search).toBe('?path=Research'))
  act(() => { history.pushState(null, '', '/'); dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(location.pathname).toBe('/'))
  expect(screen.getByRole('navigation', { name: 'Current directory' }).textContent).toBe('Home')
})

it('completes a delayed explicit recent-open event after returning Home without another open', async () => {
  history.replaceState(null, '', '/notebooks/first')
  let completeVisit!: () => void
  const visit = new Promise<void>(resolve => { completeVisit = resolve })
  const event = vi.fn()
  addEventListener('europa:recent-visit-recorded', event)
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/opened') && init?.method === 'POST') { await visit; return new Response('{}') }
    return new Response(JSON.stringify(url.includes('/workspace?') ? { path: '', items: [], next_cursor: null } : null))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<Navigation openNotebook={async () => true} renderNotebook={() => <p>Editor</p>} />)
  await screen.findByText('Editor')
  fireEvent.click(screen.getByRole('link', { name: 'Europa' }))
  await screen.findByRole('tab', { name: 'Files' })
  expect(event).not.toHaveBeenCalled()
  await act(async () => { completeVisit() })
  await waitFor(() => expect(event).toHaveBeenCalledTimes(1))
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/opened'))).toHaveLength(1)
  removeEventListener('europa:recent-visit-recorded', event)
})

it('uses a verified physical directory as the close target after a direct notebook open', async () => {
  history.replaceState(null, '', '/notebooks/physical')
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/location') ? { path: 'Work/Nested/Note.ipynb' } : { path: '', items: [], next_cursor: null })))
  render(<Navigation openNotebook={async () => true} renderNotebook={(_, navigate, closeTarget) => <button onClick={() => void navigate(closeTarget ?? '/')}>Close notebook</button>} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Close notebook' }))
  await waitFor(() => expect(location.pathname + location.search).toBe('/browse?path=Work%2FNested'))
})

it('records a successful deep-linked open once and keeps editor visible when history recording fails', async () => {
  history.replaceState(null, '', '/notebooks/first')
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify({ detail: 'Unavailable' }), { status: url.endsWith('/opened') ? 503 : 200 }))
  vi.stubGlobal('fetch', fetcher)
  render(<Navigation openNotebook={async () => true} renderNotebook={() => <p>Editor</p>} />)
  expect(await screen.findByText('Editor')).toBeTruthy()
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/opened'))).toHaveLength(1)
})

it('does not change URL on declined notebook open', async () => {
  history.replaceState(null, '', '/notebooks')
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : url.includes('/notebooks/example') ? { metadata: { europa: { notebook_id: 'example' } } } : [{ notebook_id: 'example', title: 'Example', revision: 1 }])))
  render(<Navigation openNotebook={async () => false} renderNotebook={() => <p>Editor</p>} />)
  await screen.findByRole('link', { name: 'Example' })
  fireEvent.click(screen.getByRole('link', { name: 'Example' }))
  await waitFor(() => expect(location.pathname).toBe('/notebooks'))
})

it('does not record rejected opens or navigation to Home, and guards wordmark cancellation', async () => {
  history.replaceState(null, '', '/notebooks/first')
  const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }))
  vi.stubGlobal('fetch', fetcher)
  let allow = false
  render(<Navigation openNotebook={async id => id === 'first'} beforeLeave={() => allow} renderNotebook={(_, navigate) => <button onClick={() => void navigate('/notebooks/missing')}>Switch</button>} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Switch' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/notebook/i))
  fireEvent.click(screen.getByRole('link', { name: 'Europa' }))
  expect(location.pathname).toBe('/notebooks/first')
  allow = true
  fireEvent.click(screen.getByRole('link', { name: 'Europa' }))
  await waitFor(() => expect(location.pathname).toBe('/'))
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/opened'))).toHaveLength(1)
})

it('keeps the editor URL when leaving with drafts is declined', async () => {
  history.replaceState(null, '', '/notebooks/example')
  render(<Navigation openNotebook={async () => true} beforeLeave={() => false} renderNotebook={() => <p>Editor</p>} />)
  await screen.findByText('Editor')
  fireEvent.click(screen.getByRole('link', { name: 'Europa' }))
  expect(location.pathname).toBe('/notebooks/example')
  expect(screen.getByText('Editor')).toBeTruthy()
})

it('does not render the editor for a missing deep-linked notebook', async () => {
  history.replaceState(null, '', '/notebooks/missing')
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }))
  render(<Navigation openNotebook={async () => false} renderNotebook={() => <p>Editor</p>} />)
  expect(await screen.findByRole('heading', { name: 'Notebook not found' })).toBeTruthy()
  expect(screen.queryByText('Editor')).toBeNull()
})

it('loads a different project when navigating from one project URL to another', async () => {
  history.replaceState(null, '', '/projects/one')
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'First', status: 'active', notebooks: [] } } : url.endsWith('/projects/two') ? { id: 'two', revision: 1, data: { title: 'Second', status: 'active', notebooks: [] } } : url.endsWith('/notebooks') ? [] : { items: [], next_cursor: null })))
  render(<Navigation openNotebook={async () => true} renderNotebook={() => null} />)
  await screen.findByRole('heading', { name: 'First' })
  act(() => { history.pushState(null, '', '/projects/two'); dispatchEvent(new PopStateEvent('popstate')) })
  expect(await screen.findByRole('heading', { name: 'Second' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'First' })).toBeNull()
})

it('keeps the previous notebook editor visible when another notebook is missing', async () => {
  history.replaceState(null, '', '/notebooks/first')
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }))
  render(<Navigation openNotebook={async id => id === 'first'} renderNotebook={(_, navigate) => <button onClick={() => void navigate('/notebooks/missing')}>Switch</button>} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Switch' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/notebook/i))
  expect(location.pathname).toBe('/notebooks/first')
  expect(screen.getByRole('button', { name: 'Switch' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Notebook not found' })).toBeNull()
})

it('restores the previous editor after a failed browser-history notebook switch', async () => {
  history.replaceState(null, '', '/notebooks/first')
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }))
  render(<Navigation openNotebook={async id => id === 'first'} renderNotebook={() => <p>First editor</p>} />)
  await screen.findByText('First editor')
  act(() => { history.pushState(null, '', '/notebooks/missing'); dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(location.pathname).toBe('/notebooks/first'))
  expect(screen.getByText('First editor')).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Notebook not found' })).toBeNull()
})

it('serializes browser history with an in-flight notebook open', async () => {
  history.replaceState(null, '', '/notebooks')
  let complete!: (ok: boolean) => void
  const opened = new Promise<boolean>(resolve => { complete = resolve })
  const opening = vi.fn(() => opened)
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/location') ? null : url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'target', title: 'Target', revision: 1 }])))
  render(<Navigation openNotebook={opening} renderNotebook={() => <p>Target editor</p>} />)
  fireEvent.click(await screen.findByRole('link', { name: 'Target' }))
  await waitFor(() => expect(opening).toHaveBeenCalledTimes(1))
  act(() => { history.pushState(null, '', '/?view=running'); dispatchEvent(new PopStateEvent('popstate')) })
  expect(location.pathname).toBe('/notebooks')
  await act(async () => { complete(true) })
  expect(location.pathname).toBe('/notebooks/target')
  expect(screen.getByText('Target editor')).toBeTruthy()
})
