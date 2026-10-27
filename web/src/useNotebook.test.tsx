// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useNotebook, type Notebook } from './useNotebook'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers() })
const document = (): Notebook => ({ metadata: { europa: { notebook_id: 'nb', title: 'Notebook', revision: 2 } }, cells: [{ id: 'cell', cell_type: 'code', source: 'original', metadata: {}, outputs: [] }] })

describe('revision-aware notebook controller', () => {
  it('retains browser drafts and pauses autosave when the saved revision changes', async () => {
    localStorage.setItem('europa.notebook', 'nb')
    localStorage.setItem('europa.drafts.nb', JSON.stringify({ revision: 1, drafts: { cell: 'my unsaved work' } }))
    const writes: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method) writes.push(init)
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : document()))
    }))
    const hook = renderHook(useNotebook)
    await waitFor(() => expect(hook.result.current.remote).not.toBeNull())
    expect(hook.result.current.drafts.cell).toBe('my unsaved work')
    expect(hook.result.current.paused).toBe(true)
    expect(writes).toHaveLength(0)
    hook.unmount()
    const reopened = renderHook(useNotebook)
    await waitFor(() => expect(reopened.result.current.remote).not.toBeNull())
    expect(reopened.result.current.paused).toBe(true)
    expect(reopened.result.current.drafts.cell).toBe('my unsaved work')
  })
  it('serializes double-save clicks into one revision-checked mutation', async () => {
    let saved = document()
    const revisions: number[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)); revisions.push(body.revision)
        saved = { ...saved, metadata: { europa: { ...saved.metadata.europa, revision: body.revision + 1 } }, cells: saved.cells.map(c => ({ ...c, source: body.source })) }
      }
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : saved))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    act(() => hook.result.current.edit('cell', 'new source'))
    await act(async () => { await Promise.all([hook.result.current.save(), hook.result.current.save()]) })
    expect(revisions).toEqual([2])
    expect(hook.result.current.doc?.cells[0].source).toBe('new source')
    expect(hook.result.current.dirty).toBe(false)
    expect(localStorage.getItem('europa.drafts.nb')).toBeNull()
  })
  it('refreshes applied output without dropping an unsaved draft', async () => {
    vi.useFakeTimers()
    let saved = document()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
      url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'connected', kernels: 1 } : saved,
    ))))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    act(() => { hook.result.current.setAutosave(false); hook.result.current.edit('cell', 'unsaved edit') })
    saved = { ...saved, metadata: { europa: { ...saved.metadata.europa, revision: 3 } }, cells: saved.cells.map(c => ({ ...c, outputs: [{ output_type: 'stream', name: 'stdout', text: '42\n' }] })) }
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(hook.result.current.doc?.cells[0].outputs?.[0].text).toBe('42\n')
    expect(hook.result.current.drafts.cell).toBe('unsaved edit')
    expect(hook.result.current.paused).toBe(false)
  })
})
