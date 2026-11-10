// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TextFileEditor } from './TextFileEditor'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

it('preserves a draft after reload, saves with observed revision and does not execute Python', async () => {
  const sent: { url: string; body?: string }[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => { sent.push({ url, body: init?.body as string }); return new Response(JSON.stringify(init?.method === 'POST' ? { path: 'run.py', content: 'edited', revision: 'b'.repeat(64) } : { path: 'run.py', content: 'original', revision: 'a'.repeat(64) })) })
  const view = render(<TextFileEditor path="run.py" navigate={async () => true} />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'File contents' }), { target: { value: 'edited' } })
  view.unmount()
  render(<TextFileEditor path="run.py" navigate={async () => true} />)
  expect((await screen.findByRole('textbox', { name: 'File contents' }) as HTMLTextAreaElement).value).toBe('edited')
  fireEvent.click(screen.getByRole('button', { name: 'Save text file' }))
  await waitFor(() => expect(sent.some(item => item.url.endsWith('/workspace/text/save'))).toBe(true))
  expect(JSON.parse(sent.find(item => item.url.endsWith('/workspace/text/save'))!.body!)).toMatchObject({ path: 'run.py', content: 'edited', revision: 'a'.repeat(64), operation_id: expect.any(String) })
  expect(sent.every(item => !item.url.includes('/executions'))).toBe(true)
})

it('keeps a failed save pending for exact replay and blocks another edit', async () => {
  const sent: string[] = []
  let fail = true
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { sent.push(init.body as string); if (fail) throw Error('offline'); return new Response(JSON.stringify({ path: 'x.txt', content: 'changed', revision: 'b'.repeat(64) })) } return new Response(JSON.stringify({ path: 'x.txt', content: 'old', revision: 'a'.repeat(64) })) })
  render(<TextFileEditor path="x.txt" navigate={async () => true} />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'File contents' }), { target: { value: 'changed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save text file' }))
  expect(await screen.findByRole('button', { name: 'Retry exact save' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Save text file' }).hasAttribute('disabled')).toBe(true)
  fail = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry exact save' }))
  await waitFor(() => expect(sent).toHaveLength(2))
  expect(sent[0]).toBe(sent[1])
})

it('preserves the draft and exact operation after a revision conflict', async () => {
  vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === 'POST' ? { detail: 'Text file changed externally' } : { path: 'x.txt', content: 'old', revision: 'a'.repeat(64) }), { status: init?.method === 'POST' ? 409 : 200 }))
  render(<TextFileEditor path="x.txt" navigate={async () => true} />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'File contents' }), { target: { value: 'mine' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save text file' }))
  expect((await screen.findByRole('alert')).textContent).toMatch(/changed externally/)
  expect(localStorage.getItem('europa.workspace.draft.x.txt')).toBe('mine')
  expect(screen.getByRole('button', { name: 'Save text file' }).hasAttribute('disabled')).toBe(true)
})
