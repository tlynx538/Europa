// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Home, recentVisitRecorded } from './Home'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('ignores an older recent response after a completed visit refresh and removes its listener on unmount', async () => {
  let releaseOld!: (value: Response) => void
  const old = new Promise<Response>(resolve => { releaseOld = resolve })
  let recentCalls = 0
  vi.stubGlobal('fetch', async (url: string) => url.endsWith('/recent') ? ++recentCalls === 1 ? old : new Response(JSON.stringify({ items: [{ notebook_id: 'a', opened_at: 1700000000 }] })) : new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'a', title: 'Alpha', revision: 1 }])))
  const view = render(<Home navigate={async () => true} />)
  await screen.findByRole('heading', { name: 'Welcome to Europa' })
  act(() => { dispatchEvent(new Event(recentVisitRecorded)) })
  await screen.findByRole('region', { name: 'Recent notebooks' })
  await waitFor(() => expect(screen.getByRole('region', { name: 'Recent notebooks' }).querySelector('a')?.textContent).toBe('Alpha'))
  await act(async () => { releaseOld(new Response(JSON.stringify({ items: [] }))) })
  await waitFor(() => expect(recentCalls).toBe(2))
  expect(screen.getByRole('region', { name: 'Recent notebooks' }).querySelector('a')?.textContent).toBe('Alpha')
  view.unmount()
  dispatchEvent(new Event(recentVisitRecorded))
  expect(recentCalls).toBe(2)
})

it('shows server recents in order with actual timestamps, missing recovery and unknown runtime status', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/recent') ? { items: [{ notebook_id: 'gone', opened_at: 1700000100 }, { notebook_id: 'a', opened_at: 1700000000 }] } : url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'a', title: 'Alpha', revision: 1 }])))
  render(<Home navigate={async () => true} />)
  expect(await screen.findByText(/Missing notebook/)).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Recent notebooks' })).getByRole('link', { name: 'Alpha' })).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Recent notebooks' })).getAllByText(/Runtime: Unknown/)).toHaveLength(2)
  expect(document.querySelector('time')?.getAttribute('datetime')).toBe(new Date(1700000100 * 1000).toISOString())
  expect(screen.getByRole('heading', { name: 'Welcome to Europa' })).toBeTruthy()
  const recent = screen.getByRole('region', { name: 'Recent notebooks' })
  expect(within(recent).getByText('Location')).toBeTruthy()
  expect(within(recent).getByText('Last opened')).toBeTruthy()
  expect(within(recent).getByText('Unavailable')).toBeTruthy()
  expect(within(recent).getAllByText('Standalone')).toHaveLength(1)
  expect(within(recent).getAllByTitle('Runtime status unknown')).toHaveLength(2)
})

it('does not turn a failed recent request into an empty history and still displays project folders', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/recent') ? { detail: 'Offline' } : url.includes('/projects?') ? { items: [{ id: 'p', revision: 1, data: { title: 'Research', status: 'active', notebooks: [] } }], next_cursor: null } : []), { status: url.includes('/recent') ? 503 : 200 }))
  render(<Home navigate={async () => true} />)
  expect(await screen.findByRole('link', { name: 'Research' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Research' }).querySelector('[aria-hidden="true"]')).toBeTruthy()
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText(/No recent notebooks/)).toBeNull()
})

it('creates a project via the revision-safe change API and opens its folder', async () => {
  const navigate = vi.fn(async () => true), calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('prompt', () => 'Research')
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => { calls.push({ url, init }); return new Response(JSON.stringify(init?.method === 'POST' ? { id: 'p', revision: 1, data: { title: 'Research', status: 'active', notebooks: [] } } : url.includes('/recent') ? { items: [] } : url.includes('/projects?') ? { items: [], next_cursor: null } : [])) })
  render(<Home navigate={navigate} />)
  fireEvent.click(await screen.findByRole('button', { name: 'New legacy project' }))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/projects/p'))
  expect(JSON.parse(calls.find(c => c.init?.method === 'POST')!.init!.body as string)).toMatchObject({ title: 'Research', operation_id: expect.any(String) })
})

it('searches standalone files and projects and sorts notebook rows', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/recent') ? { items: [] } : url.includes('/projects?') ? { items: [{ id: 'p', data: { title: 'Research', status: 'active', notebooks: [{ notebook_id: 'a', present: true }] } }], next_cursor: null } : [{ notebook_id: 'a', title: 'Alpha', revision: 1 }, { notebook_id: 'b', title: 'Beta', revision: 2 }, { notebook_id: 'c', title: 'Gamma', revision: 3 }])))
  render(<Home navigate={async () => true} />)
  expect(await screen.findByRole('link', { name: 'Research' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Research' }).querySelector('[aria-hidden="true"]')).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort notebooks' }), { target: { value: 'title-desc' } })
  expect(within(screen.getByRole('region', { name: 'Standalone notebooks' })).getAllByRole('link').map(a => a.textContent)).toEqual(['Gamma', 'Beta'])
  expect(screen.getByRole('link', { name: 'Gamma' }).querySelector('[aria-hidden="true"]')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search files' }), { target: { value: 'beta' } })
  expect(screen.queryByRole('link', { name: 'Research' })).toBeNull()
  expect(within(screen.getByRole('region', { name: 'Standalone notebooks' })).getAllByRole('link').map(a => a.textContent)).toEqual(['Beta'])
})

it('creates a standalone notebook with no runtime request and navigates to it', async () => {
  const navigate = vi.fn(async () => true), sent: string[] = []
  vi.stubGlobal('prompt', () => 'Notes')
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => { sent.push(`${init?.method ?? 'GET'} ${url}`); return new Response(JSON.stringify(init?.method === 'POST' ? { metadata: { europa: { notebook_id: 'book' } } } : url.includes('/recent') ? { items: [] } : url.includes('/projects?') ? { items: [], next_cursor: null } : [])) })
  render(<Home navigate={navigate} />)
  fireEvent.click(screen.getByRole('button', { name: 'New notebook' }))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/notebooks/book'))
  expect(sent.filter(call => call.startsWith('POST'))).toEqual(['POST /api/notebooks'])
})

it('retains a created notebook in Home if the editor declines to open it', async () => {
  vi.stubGlobal('prompt', () => 'Notes')
  let created = false
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') created = true
    return new Response(JSON.stringify(init?.method === 'POST' ? { metadata: { europa: { notebook_id: 'book' } } } : url.includes('/recent') ? { items: [] } : url.includes('/projects?') ? { items: [], next_cursor: null } : created ? [{ notebook_id: 'book', title: 'Notes', revision: 1 }] : []))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<Home navigate={async () => false} />)
  fireEvent.click(screen.getByRole('button', { name: 'New notebook' }))
  expect(await screen.findByRole('link', { name: 'Notes' })).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toMatch(/created but could not be opened/)
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
