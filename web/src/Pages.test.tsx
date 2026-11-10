// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ActivityPage, NotebookLibrary, ProjectDetail, ProjectsPage } from './Pages'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('filters project records by title without altering the server list', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ items: [
    { id: 'one', data: { title: 'Research', status: 'active', notebooks: [] } },
    { id: 'two', data: { title: 'Design', status: 'archived', notebooks: [] } },
  ], next_cursor: null })))
  render(<ProjectsPage navigate={async () => true} />)
  await screen.findByRole('link', { name: 'Research' })
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search projects' }), { target: { value: 'design' } })
  expect(screen.queryByRole('link', { name: 'Research' })).toBeNull()
  expect(screen.getByRole('link', { name: 'Design' })).toBeTruthy()
})

it('requires confirmation before deleting a saved notebook', async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'one', title: 'Research', revision: 3 }])))
  vi.stubGlobal('fetch', fetcher)
  vi.stubGlobal('confirm', () => false)
  render(<NotebookLibrary navigate={async () => true} />)
  await screen.findByRole('link', { name: 'Research' })
  fireEvent.keyDown(screen.getByRole('button', { name: 'Actions for Research' }), { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
})

it('allows deletion of the previously selected notebook from the library and clears selection', async () => {
  localStorage.setItem('europa.notebook', 'one')
  const removed = vi.fn()
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => new Response(init?.method === 'DELETE' ? null : JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'one', title: 'Research', revision: 3 }]), { status: init?.method === 'DELETE' ? 204 : 200 }))
  vi.stubGlobal('fetch', fetcher)
  vi.stubGlobal('confirm', () => true)
  render(<NotebookLibrary currentNotebookId="one" onNotebookDeleted={removed} navigate={async () => true} />)
  fireEvent.keyDown(await screen.findByRole('button', { name: 'Actions for Research' }), { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
  await waitFor(() => expect(removed).toHaveBeenCalledWith('one'))
  expect(localStorage.getItem('europa.notebook')).toBeNull()
  expect(fetcher.mock.calls.some(([url, init]) => url.includes('/notebooks/one?revision=3') && init?.method === 'DELETE')).toBe(true)
})

it('blocks deletion while the notebook has recoverable browser drafts', async () => {
  localStorage.setItem('europa.drafts.one', JSON.stringify({ revision: 3, drafts: { cell: 'unsaved' } }))
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [{ notebook_id: 'one', title: 'Research', revision: 3 }])))
  render(<NotebookLibrary currentNotebookId="one" navigate={async () => true} />)
  fireEvent.keyDown(await screen.findByRole('button', { name: 'Actions for Research' }), { key: 'ArrowDown' })
  const button = await screen.findByRole('menuitem', { name: 'Delete' })
  expect(button.getAttribute('data-disabled')).not.toBeNull()
  expect(button.getAttribute('title')).toMatch(/drafts/i)
})

it('shows a missing project rather than a generic transport error', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }))
  render(<ProjectDetail id="missing" navigate={async () => true} />)
  expect(await screen.findByRole('heading', { name: 'Project not found' })).toBeTruthy()
})

it('does not treat a failed scoped list as a missing project', async () => {
  vi.stubGlobal('fetch', async (url: string) => url.includes('/roadmaps?')
    ? new Response(JSON.stringify({ detail: 'Roadmaps unavailable' }), { status: 404 })
    : new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'One', status: 'active', notebooks: [] } } : url.endsWith('/notebooks') ? [] : { items: [], next_cursor: null })))
  render(<ProjectDetail id="one" navigate={async () => true} />)
  expect(await screen.findByRole('heading', { name: 'One' })).toBeTruthy()
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Project not found' })).toBeNull()
})

it('only offers notebooks not already assigned to another project', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'One', status: 'active', notebooks: [] } } : url.includes('/projects?') ? { items: [{ id: 'two', data: { title: 'Two', status: 'active', notebooks: [{ notebook_id: 'taken', present: true }] } }], next_cursor: null } : url.endsWith('/notebooks') ? [{ notebook_id: 'taken', title: 'Taken', revision: 1 }, { notebook_id: 'free', title: 'Free', revision: 1 }] : { items: [], next_cursor: null })))
  render(<ProjectDetail id="one" navigate={async () => true} />)
  const select = await screen.findByRole('combobox', { name: 'Add notebook' })
  await waitFor(() => expect(select.querySelectorAll('option')).toHaveLength(2))
  expect(screen.queryByRole('option', { name: 'Taken' })).toBeNull()
  expect(screen.getByRole('option', { name: 'Free' })).toBeTruthy()
})

it('shows project association and standalone notebook status', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [{ id: 'one', data: { title: 'Research', status: 'active', notebooks: [{ notebook_id: 'a', present: true }] } }], next_cursor: null } : [{ notebook_id: 'a', title: 'Alpha', revision: 1 }, { notebook_id: 'b', title: 'Beta', revision: 1 }])))
  render(<NotebookLibrary navigate={async () => true} />)
  expect(await screen.findByText('Research')).toBeTruthy()
  expect(screen.getByText('Standalone')).toBeTruthy()
})

it('searches and sorts the folder files while keeping project history in a closed disclosure', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'One', status: 'active', notebooks: [{ notebook_id: 'a', present: true }, { notebook_id: 'b', present: true }] } }
    : url.endsWith('/notebooks') ? [{ notebook_id: 'a', title: 'Alpha', revision: 1 }, { notebook_id: 'b', title: 'Beta', revision: 2 }]
      : { items: url.includes('/activities') ? [{ id: 'event', created_at: 1700000000, data: { summary: 'Reviewed', result: 'done' } }] : [], next_cursor: null })))
  render(<ProjectDetail id="one" navigate={async () => true} />)
  const folder = await screen.findByRole('region', { name: 'Notebooks' })
  expect(within(folder).getAllByRole('link').map(a => a.textContent)).toEqual(['Alpha', 'Beta'])
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort project notebooks' }), { target: { value: 'title-desc' } })
  expect(within(folder).getAllByRole('link').map(a => a.textContent)).toEqual(['Beta', 'Alpha'])
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search project notebooks' }), { target: { value: 'missing' } })
  expect(within(folder).queryAllByRole('link')).toHaveLength(0)
  expect(within(folder).getByText(/No notebooks match/)).toBeTruthy()
  const details = screen.getByText('Project details and history').closest('details')!
  expect(details.hasAttribute('open')).toBe(false)
  expect(details.textContent).toMatch(/Reviewed/)
})

it('sorts notebook records and shows saved revision metadata in the notebook browser', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [
    { notebook_id: 'b', title: 'Beta', revision: 2 }, { notebook_id: 'a', title: 'Alpha', revision: 5 },
  ])))
  render(<NotebookLibrary navigate={async () => true} />)
  await screen.findByRole('link', { name: 'Alpha' })
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort notebooks' }), { target: { value: 'title-desc' } })
  expect(screen.getAllByRole('link', { name: /^(Alpha|Beta)$/ }).map(link => link.textContent)).toEqual(['Beta', 'Alpha'])
  expect(screen.getByText(/Revision 5/)).toBeTruthy()
})

it('distinguishes an empty notebook library from search with no matches', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : [])))
  render(<NotebookLibrary navigate={async () => true} />)
  expect(await screen.findByText(/No notebooks yet/)).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search notebooks' }), { target: { value: 'missing' } })
  expect(screen.getByText(/No notebooks match/)).toBeTruthy()
})

it('shows server roadmap, conversation and activity in project sections', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'One', status: 'active', notebooks: [] } }
    : url.endsWith('/notebooks') ? [] : { items: url.includes('/roadmaps?') ? [{ id: 'r', data: { objective: 'Ship', progress: 50, tasks: [] } }] : url.includes('/conversations?') ? [{ id: 'c', data: { title: 'Planning' } }] : url.includes('/activities?') ? [{ id: 'a', created_at: 1700000000, data: { summary: 'Reviewed', result: 'done' } }] : [], next_cursor: null })))
  render(<ProjectDetail id="one" navigate={async () => true} />)
  expect(await screen.findByText(/Ship · 50%/)).toBeTruthy()
  expect(screen.getByText(/Planning · c · Project-wide/)).toBeTruthy()
  expect(screen.getByText('Reviewed')).toBeTruthy()
  expect(screen.getByText('done')).toBeTruthy()
})

it('does not expose project mutations for deleted projects', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/projects/one') ? { id: 'one', revision: 1, data: { title: 'One', status: 'deleted', notebooks: [] } } : url.endsWith('/notebooks') ? [] : { items: [], next_cursor: null })))
  render(<ProjectDetail id="one" navigate={async () => true} />)
  expect(await screen.findByRole('heading', { name: 'One' })).toBeTruthy()
  fireEvent.keyDown(screen.getByRole('button', { name: 'Project actions' }), { key: 'ArrowDown' })
  expect(screen.getByRole('menuitem', { name: 'Rename project' }).getAttribute('data-disabled')).not.toBeNull()
  expect(screen.queryByRole('menuitem', { name: 'Delete project' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'New notebook in project' })).toBeNull()
})

it('shows dated persisted activity in descending order with notebook and task references', async () => {
  const navigate = vi.fn(async () => true)
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/notebooks') ? [{ notebook_id: 'book', title: 'Observations', revision: 1 }] : {
    items: url.includes('/activities?') ? [
      { id: 'older', created_at: 1700000000, data: { summary: 'Started review', result: 'recorded' } },
      { id: 'newer', created_at: 1700000100, data: { summary: 'Checked results', result: 'completed', notebook_id: 'book', cell_id: 'cell-id', task_id: 'task' } },
    ] : url.includes('/roadmaps?') ? [{ id: 'plan', data: { tasks: [{ id: 'task', title: 'Review results' }] } }] : [{ id: 'project', data: { title: 'Research', notebooks: [] } }], next_cursor: null,
  })))
  render(<ActivityPage navigate={navigate} />)
  fireEvent.change(await screen.findByRole('combobox', { name: 'Project' }), { target: { value: 'project' } })
  const titles = await screen.findAllByText(/Checked results|Started review/)
  expect(titles.map(t => t.textContent)).toEqual(['Checked results', 'Started review'])
  expect(screen.getByText('Task: Review results')).toBeTruthy()
  expect(screen.getByText('Cell reference: cell-id')).toBeTruthy()
  expect(document.querySelector('time')?.getAttribute('datetime')).toBe(new Date(1700000100 * 1000).toISOString())
  fireEvent.click(screen.getByRole('link', { name: 'Observations' }))
  expect(navigate).toHaveBeenCalledWith('/notebooks/book')
})

it('offers project navigation for empty activity without a timeline', async () => {
  const navigate = vi.fn(async () => true)
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.endsWith('/notebooks') ? [] : { items: url.includes('/projects?') ? [{ id: 'project', data: { title: 'Research' } }] : [], next_cursor: null })))
  render(<ActivityPage navigate={navigate} />)
  fireEvent.change(await screen.findByRole('combobox', { name: 'Project' }), { target: { value: 'project' } })
  expect(await screen.findByText(/No activity recorded for this project/)).toBeTruthy()
  expect(screen.queryByRole('list')).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: 'Open project' }))
  expect(navigate).toHaveBeenCalledWith('/projects/project')
})

it('creates a notebook then associates it using the project revision before opening it', async () => {
  const sent: { path: string; method: string; body?: string }[] = []
  vi.stubGlobal('prompt', () => 'New notebook')
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    sent.push({ path, method: init?.method ?? 'GET', body: init?.body?.toString() })
    const data = path.endsWith('/projects/one') ? { id: 'one', revision: sent.length > 7 ? 2 : 1, data: { title: 'One', status: 'active', notebooks: [] } }
      : path.endsWith('/notebooks') && init?.method === 'POST' ? { metadata: { europa: { notebook_id: 'new', title: 'New notebook', revision: 1 } } }
        : path.endsWith('/notebooks') ? [] : path.endsWith('/projects/one/notebooks') ? { id: 'one', revision: 2 } : { items: [], next_cursor: null }
    return new Response(JSON.stringify(data))
  })
  const navigate = vi.fn(async () => true)
  render(<ProjectDetail id="one" navigate={navigate} />)
  fireEvent.click(await screen.findByRole('button', { name: 'New notebook in project' }))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/notebooks/new'))
  const created = sent.findIndex(s => s.path.endsWith('/notebooks') && s.method === 'POST')
  const assigned = sent.findIndex(s => s.path.endsWith('/projects/one/notebooks') && s.method === 'POST')
  expect(created).toBeGreaterThan(-1)
  expect(assigned).toBeGreaterThan(created)
  expect(JSON.parse(sent[assigned].body!)).toMatchObject({ revision: 1, notebook_id: 'new' })
})

it('recovers the saved notebook id after canonical initialization fails without posting a second notebook', async () => {
  vi.stubGlobal('prompt', () => 'New notebook')
  const calls: string[] = [], navigate = vi.fn(async () => true)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      calls.push(url)
      return new Response(JSON.stringify({ detail: { message: 'Conversation unavailable', notebook_id: 'a'.repeat(32), recovery: `/api/notebooks/${'a'.repeat(32)}/conversation` } }), { status: 503 })
    }
    return new Response(JSON.stringify(url.includes('/projects?') ? { items: [], next_cursor: null } : []))
  }))
  render(<NotebookLibrary navigate={navigate} />)
  fireEvent.click(screen.getByRole('button', { name: 'New notebook' }))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(`/notebooks/${'a'.repeat(32)}`))
  expect(calls).toEqual(['/api/notebooks'])
})
