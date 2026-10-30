// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Navigation } from './Navigation'

afterEach(() => { cleanup(); history.replaceState(null, '', '/'); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('redirects root to library and follows browser history', async () => {
  render(<Navigation openNotebook={async () => true} renderNotebook={() => <p>Editor</p>} />)
  expect(location.pathname).toBe('/notebooks')
  fireEvent.click(screen.getByRole('link', { name: 'Projects' }))
  expect(location.pathname).toBe('/projects')
  act(() => { history.back() })
  await waitFor(() => expect(location.pathname).toBe('/notebooks'))
  expect(screen.getByRole('link', { name: 'Notebooks' }).getAttribute('aria-current')).toBe('page')
})

it('does not change URL on declined notebook open', async () => {
  history.replaceState(null, '', '/notebooks')
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : url.includes('/notebooks/example') ? { metadata: { europa: { notebook_id: 'example' } } } : [{ notebook_id: 'example', title: 'Example', revision: 1 }])))
  render(<Navigation openNotebook={async () => false} renderNotebook={() => <p>Editor</p>} />)
  await screen.findByRole('link', { name: 'Example' })
  fireEvent.click(screen.getByRole('link', { name: 'Example' }))
  await waitFor(() => expect(location.pathname).toBe('/notebooks'))
})

it('keeps the editor URL when leaving with drafts is declined', async () => {
  history.replaceState(null, '', '/notebooks/example')
  render(<Navigation openNotebook={async () => true} beforeLeave={() => false} renderNotebook={() => <p>Editor</p>} />)
  await screen.findByText('Editor')
  fireEvent.click(screen.getByRole('link', { name: 'Projects' }))
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
