// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useNotebook, type Notebook } from './useNotebook'

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers() })
const document = (): Notebook => ({ metadata: { europa: { notebook_id: 'nb', title: 'Notebook', revision: 2 } }, cells: [{ id: 'cell', cell_type: 'code', source: 'original', metadata: {}, outputs: [] }] })

describe('revision-aware notebook controller', () => {
  it('deduplicates simultaneous opens of the same deep link', async () => {
    let opens = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/notebooks/nb') opens++
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : document()))
    }))
    const hook = renderHook(useNotebook)
    await act(async () => { const first = hook.result.current.open('nb'); const second = hook.result.current.open('nb'); expect(opens).toBe(1); expect(await first).not.toBeNull(); expect(await second).not.toBeNull() })
  })
  it('closes the editor without deleting a recoverable draft', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : document()))))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    act(() => hook.result.current.edit('cell', 'recover me'))
    act(() => hook.result.current.close())
    expect(hook.result.current.doc).toBeNull()
    expect(hook.result.current.dirty).toBe(false)
    expect(JSON.parse(localStorage.getItem('europa.drafts.nb')!).drafts.cell).toBe('recover me')
    await act(() => hook.result.current.open('nb'))
    expect(hook.result.current.drafts.cell).toBe('recover me')
  })
  it('refuses to close when browser draft recovery is missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : document()))))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    act(() => hook.result.current.edit('cell', 'not yet safely stored'))
    localStorage.removeItem('europa.drafts.nb')
    act(() => expect(hook.result.current.close()).toBe(false))
    expect(hook.result.current.doc?.metadata.europa.notebook_id).toBe('nb')
    expect(hook.result.current.drafts.cell).toBe('not yet safely stored')
  })
  it('retains browser drafts and pauses autosave when the saved revision changes', async () => {
    localStorage.setItem('europa.notebook', 'nb')
    localStorage.setItem('europa.drafts.nb', JSON.stringify({ revision: 1, drafts: { cell: 'my unsaved work' } }))
    const writes: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method) writes.push(init)
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : document()))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    await waitFor(() => expect(hook.result.current.remote).not.toBeNull())
    expect(hook.result.current.drafts.cell).toBe('my unsaved work')
    expect(hook.result.current.paused).toBe(true)
    expect(writes).toHaveLength(0)
    hook.unmount()
    const reopened = renderHook(useNotebook)
    await act(() => reopened.result.current.open('nb'))
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
  it('flushes drafted source before paste and asks the server to assign a fresh cell ID', async () => {
    let saved = document()
    const writes: { path: string; body: Record<string, unknown> }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method) {
        const body = JSON.parse(String(init.body)); writes.push({ path: url, body })
        saved = { ...saved, metadata: { europa: { ...saved.metadata.europa, revision: saved.metadata.europa.revision + 1 } }, cells: init.method === 'PATCH' ? saved.cells.map(c => ({ ...c, source: body.source })) : [...saved.cells, { id: 'server-generated', cell_type: body.cell_type, source: body.source, metadata: {} }] }
      }
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : saved))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    act(() => hook.result.current.edit('cell', 'my draft'))
    await act(() => hook.result.current.paste({ cell_type: 'code', source: 'copy' }, 1))
    expect(writes.map(w => w.path)).toEqual(['/api/notebooks/nb/cells/cell', '/api/notebooks/nb/cells'])
    expect(writes[1].body).toEqual({ revision: 3, cell_type: 'code', source: 'copy', index: 1 })
    expect(hook.result.current.doc?.cells[1].id).toBe('server-generated')
    expect(hook.result.current.dirty).toBe(false)
  })
  it('submits run all in saved code cell order and skips markdown', async () => {
    let saved: Notebook = { ...document(), cells: [document().cells[0], { id: 'note', cell_type: 'markdown', source: 'text', metadata: {} }, { id: 'last', cell_type: 'code', source: 'print(2)', metadata: {}, outputs: [] }] }
    const submitted: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/execute') && init?.method === 'POST') {
        submitted.push(url)
        saved = { ...saved, metadata: { europa: { ...saved.metadata.europa, revision: saved.metadata.europa.revision + 1 } } }
        return new Response(JSON.stringify({ execution_id: `r${submitted.length}`, cell_id: submitted.length === 1 ? 'cell' : 'last', state: 'applied', result: { status: 'ok' } }))
      }
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : saved))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    await act(() => hook.result.current.runAll())
    expect(submitted).toEqual(['/api/notebooks/nb/cells/cell/execute', '/api/notebooks/nb/cells/last/execute'])
  })
  it('stops run all after an applied execution error instead of submitting the next cell', async () => {
    const saved = { ...document(), cells: [...document().cells, { id: 'last', cell_type: 'code' as const, source: 'print(2)', metadata: {}, outputs: [] }] }
    const submitted: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/execute') && init?.method === 'POST') {
        submitted.push(url)
        return new Response(JSON.stringify({ execution_id: 'error', cell_id: 'cell', state: 'applied', result: { status: 'error' } }))
      }
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : saved))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    await act(() => hook.result.current.runAll())
    expect(submitted).toEqual(['/api/notebooks/nb/cells/cell/execute'])
    expect(hook.result.current.error).toContain('Run all stopped')
  })
  it('reports an unknown outcome when receipt polling loses transport and never runs another cell', async () => {
    const saved = { ...document(), cells: [...document().cells, { id: 'last', cell_type: 'code' as const, source: 'print(2)', metadata: {}, outputs: [] }] }
    const submitted: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/execute') && init?.method === 'POST') { submitted.push(url); return new Response(JSON.stringify({ execution_id: 'pending', cell_id: 'cell', state: 'running' })) }
      if (url.endsWith('/executions/pending')) throw new TypeError('offline')
      return new Response(JSON.stringify(url.includes('/executions?') ? { items: [], next_offset: null } : url.endsWith('/session') ? { state: 'disconnected', kernels: 0 } : saved))
    }))
    const hook = renderHook(useNotebook)
    await act(() => hook.result.current.open('nb'))
    await act(() => hook.result.current.runAll())
    expect(submitted).toEqual(['/api/notebooks/nb/cells/cell/execute'])
    expect(hook.result.current.error).toMatch(/outcome unknown; it may still be running/i)
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
