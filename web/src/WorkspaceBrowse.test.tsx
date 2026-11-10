// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkspaceBrowse } from './WorkspaceBrowse'
import { useState } from 'react'

function Browser() {
  const [path, setPath] = useState('')
  return <WorkspaceBrowse path={path} navigate={async url => { setPath(new URL(url, 'http://localhost').searchParams.get('path') ?? ''); return true }} />
}

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

it('opens real folders, paginates and never treats a folder as a project', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', async (url: string) => { calls.push(url); const query = new URL(url, 'http://localhost').searchParams; return new Response(JSON.stringify(query.get('path') === 'Research' ? { path: 'Research', items: [{ name: 'notes.py', type: 'file', size: 5, modified: 0 }], next_cursor: null } : query.has('cursor') ? { path: '', items: [{ name: 'other.txt', type: 'file', size: 1, modified: 0 }], next_cursor: null } : { path: '', items: [{ name: 'Research', type: 'directory', size: null, modified: 0 }], next_cursor: 'Research' })) })
  render(<Browser />)
  fireEvent.click(await screen.findByRole('button', { name: 'Research' }))
  expect(await screen.findByRole('button', { name: 'notes.py' })).toBeTruthy()
  expect(screen.queryByText(/project welcome/i)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Workspace root' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more' }))
  expect(await screen.findByRole('button', { name: 'other.txt' })).toBeTruthy()
  expect(calls.some(call => call.includes('cursor=Research'))).toBe(true)
})

it('reports inaccessible folders without claiming an empty directory', async () => {
  vi.stubGlobal('fetch', async (url: string) => new Response(JSON.stringify(url.includes('path=Locked') ? { detail: 'Workspace entry inaccessible' } : { path: '', items: [{ name: 'Locked', type: 'directory' }], next_cursor: null }), { status: url.includes('path=Locked') ? 403 : 200 }))
  render(<Browser />)
  fireEvent.click(await screen.findByRole('button', { name: 'Locked' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/inaccessible/))
  expect(screen.queryByText('Empty folder')).toBeNull()
})

it('creates a real folder using an operation id, then refreshes the listing', async () => {
  const sent: string[] = []
  vi.stubGlobal('prompt', () => 'Folder A')
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { sent.push(init.body as string); return new Response(JSON.stringify({ path: 'New folder' }), { status: 201 }) }
    return new Response(JSON.stringify({ path: '', items: sent.length ? [{ name: 'Folder A', type: 'directory' }] : [], next_cursor: null }))
  })
  render(<Browser />)
  fireEvent.click(await screen.findByRole('button', { name: 'New folder' }))
  expect(await screen.findByRole('button', { name: 'Folder A' })).toBeTruthy()
  expect(JSON.parse(sent[0])).toMatchObject({ path: 'Folder A', operation_id: expect.any(String) })
})
