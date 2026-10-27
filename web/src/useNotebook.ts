import { useEffect, useRef, useState } from 'react'
import { ApiError, json, request, terminalReceipt, type Receipt } from './api'
import type { Output } from './Output'

export type Meta = { notebook_id: string; title: string; revision: number }
export type Cell = { id: string; cell_type: 'code' | 'markdown'; source: string | string[]; execution_count?: number | null; metadata: { europa?: { execution_id: string; status: string }; [key: string]: unknown }; outputs?: Output[] }
export type Notebook = { metadata: { europa: Meta }; cells: Cell[] }
export const source = (cell: Cell) => Array.isArray(cell.source) ? cell.source.join('') : cell.source
type Drafts = Record<string, string>
const draftKey = (id: string) => `europa.drafts.${id}`

export function useNotebook() {
  const [doc, setDoc] = useState<Notebook | null>(null)
  const [drafts, setDrafts] = useState<Drafts>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [remote, setRemote] = useState<Notebook | null>(null)
  const [paused, updatePaused] = useState(false)
  const review = useRef(false)
  const setPaused = (value: boolean) => { review.current = value; updatePaused(value) }
  const [autosave, setAutosave] = useState(true)
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [connection, setConnection] = useState('disconnected')
  const [session, setSession] = useState({ state: 'disconnected', kernels: 0 })
  const current = useRef(doc), local = useRef(drafts), locked = useRef(false), epoch = useRef(0)
  const docUpdate = (value: Notebook | null) => { current.current = value; setDoc(value) }
  const persist = (value: Drafts, document = current.current) => {
    local.current = value; setDrafts(value)
    if (!document) return
    try {
      if (Object.keys(value).length) localStorage.setItem(draftKey(document.metadata.europa.notebook_id), JSON.stringify({ revision: document.metadata.europa.revision, drafts: value, review: review.current }))
      else localStorage.removeItem(draftKey(document.metadata.europa.notebook_id))
    } catch { setError('Browser draft storage unavailable. Keep the page open and save explicitly.'); setPaused(true) }
  }
  const dirty = Object.keys(drafts).length > 0
  const id = doc?.metadata.europa.notebook_id
  const active = receipts.some(r => !terminalReceipt(r.state))

  const open = async (notebook: string, discard = false) => {
    if (locked.current) return
    if (dirty && !discard && !confirm('Keep drafts in this browser and switch notebooks?')) return
    const token = ++epoch.current
    locked.current = true; setBusy(true)
    try {
      const next = await request<Notebook>(`/notebooks/${notebook}`)
      if (token !== epoch.current) return
      let restored: Drafts = {}, revision = next.metadata.europa.revision, needsReview = false
      if (!discard) {
        const stored = localStorage.getItem(draftKey(notebook))
        if (stored) { const parsed = JSON.parse(stored); restored = parsed.drafts; revision = parsed.revision; needsReview = parsed.review === true }
      }
      const conflict = Object.keys(restored).length > 0 && (needsReview || revision !== next.metadata.europa.revision)
      setPaused(conflict)
      docUpdate(next); persist(restored, next); setReceipts([]); setRemote(null); setError('')
      if (conflict) { setRemote(next); setError('Saved revision changed or drafts require review. Review recovered drafts explicitly.') }
      localStorage.setItem('europa.notebook', notebook)
    } catch (e) { setError(String(e)) } finally { locked.current = false; setBusy(false) }
  }
  useEffect(() => {
    const last = localStorage.getItem('europa.notebook')
    if (last) void open(last)
    // Restore once; later changes are deliberate navigation.
  }, [])

  async function action(fn: () => Promise<void>) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError('')
    try { await fn() } catch (e) {
      setPaused(true)
      persist(local.current)
      setError(e instanceof ApiError && e.status === 409 ? 'Revision conflict or notebook busy. Drafts retained; compare saved state before retrying.' : String(e))
    } finally { locked.current = false; setBusy(false) }
  }
  const edit = (cell: string, value: string) => {
    const next = { ...local.current }
    const saved = current.current?.cells.find(c => c.id === cell)
    if (saved && source(saved) === value) delete next[cell]; else next[cell] = value
    persist(next)
  }
  async function saveDrafts() {
    const snapshot = { ...local.current }
    for (const [cell, value] of Object.entries(snapshot)) {
      const before = current.current!
      if (!before.cells.some(c => c.id === cell)) throw new Error('A drafted cell was deleted. Copy or discard its draft in the conflict panel.')
      const next = await request<Notebook>(`/notebooks/${before.metadata.europa.notebook_id}/cells/${cell}`, json('PATCH', { revision: before.metadata.europa.revision, source: value }))
      docUpdate(next)
      const rest = { ...local.current }
      if (rest[cell] === value) delete rest[cell]
      persist(rest, next)
    }
  }
  const save = () => action(saveDrafts)
  useEffect(() => {
    if (!autosave || !dirty || busy || paused || active || remote) return
    const timer = setTimeout(() => void save(), 1000)
    return () => clearTimeout(timer)
  }, [autosave, drafts, busy, paused, active, remote])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const acceptRefresh = (next: Notebook) => {
    const before = current.current
    if (!before || before.metadata.europa.notebook_id !== next.metadata.europa.notebook_id || locked.current || review.current) return
    if (before.metadata.europa.revision === next.metadata.europa.revision) return
    const conflict = Object.keys(local.current).some(cell => {
      const old = before.cells.find(c => c.id === cell), fresh = next.cells.find(c => c.id === cell)
      return !old || !fresh || source(old) !== source(fresh) || old.cell_type !== fresh.cell_type
    })
    if (conflict) { setRemote(next); setPaused(true); persist(local.current); setError('Revision conflict: saved source changed. Local drafts were preserved.') }
    else { docUpdate(next); persist(local.current, next) }
  }
  const acceptRefreshRef = useRef(acceptRefresh); acceptRefreshRef.current = acceptRefresh
  useEffect(() => {
    if (!id) return
    setConnection('connecting'); setSession({ state: 'checking', kernels: 0 })
    let stopped = false
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const history: Receipt[] = []
        let offset: number | null = 0
        do {
          const page: { items: Receipt[]; next_offset: number | null } = await request(`/notebooks/${id}/executions?offset=${offset}&limit=100`, { signal: controller.signal })
          history.push(...page.items); offset = page.next_offset
        } while (offset !== null && !stopped)
        const [saved, live] = await Promise.all([
          request<Notebook>(`/notebooks/${id}`, { signal: controller.signal }),
          request<{ state: string; kernels: number }>(`/notebooks/${id}/session`, { signal: controller.signal }),
        ])
        if (!stopped) { setReceipts(history); setSession(live); setConnection('connected'); acceptRefreshRef.current(saved) }
      } catch { if (!stopped) { setConnection('reconnecting — execution is not cancelled'); setSession({ state: 'unknown (connection lost)', kernels: 0 }) } }
      finally { if (!stopped) timer = setTimeout(poll, 1500) }
    }
    void poll()
    return () => { stopped = true; controller.abort(); clearTimeout(timer) }
  }, [id])

  const execute = (cell: string) => action(async () => {
    if (paused || remote) throw new Error('Resolve the saved revision conflict before running.')
    await saveDrafts()
    const before = current.current!
    let receipt: Receipt
    try { receipt = await request(`/notebooks/${before.metadata.europa.notebook_id}/cells/${cell}/execute`, json('POST', { revision: before.metadata.europa.revision })) }
    catch (e) {
      if (e instanceof ApiError) throw e
      throw new Error('Submission response lost. Check receipt history before explicitly running again; execution may have been accepted.')
    }
    setReceipts(prev => [...prev.filter(r => r.execution_id !== receipt.execution_id), receipt])
    const next = await request<Notebook>(`/notebooks/${before.metadata.europa.notebook_id}`)
    docUpdate(next); persist(local.current, next)
  })
  const mutate = (path: string, body: object, method = 'POST') => action(async () => {
    if (paused || remote) throw new Error('Resolve the conflict first.')
    await saveDrafts()
    const before = current.current!
    docUpdate(await request<Notebook>(`/notebooks/${before.metadata.europa.notebook_id}${path}`, json(method, { revision: before.metadata.europa.revision, ...body })))
  })
  const compare = () => action(async () => { setRemote(await request<Notebook>(`/notebooks/${id}`)); setPaused(true) })
  const resolve = (keep: boolean) => {
    if (!remote) return
    setPaused(keep); docUpdate(remote); persist(keep ? local.current : {}, remote); setRemote(null)
    setError(keep ? 'Drafts rebased for review. Save explicitly to accept them; autosave remains paused.' : '')
  }
  return { doc, drafts, busy, error, remote, paused, autosave, setAutosave, dirty, receipts, connection, session, active,
    edit, open, save, execute, mutate, action, compare, resolve, setError,
    resume: () => { setPaused(false); setError('') },
    create: (title: string) => action(async () => {
      const next = await request<Notebook>('/notebooks', json('POST', { title }))
      docUpdate(next); persist({}, next); setReceipts([]); setPaused(false); setRemote(null)
      localStorage.setItem('europa.notebook', next.metadata.europa.notebook_id)
    }),
    remove: (cell: string) => action(async () => {
      if (dirty) throw new Error('Save drafts before deleting a cell.')
      const before = current.current!
      docUpdate(await request<Notebook>(`/notebooks/${id}/cells/${cell}?revision=${before.metadata.europa.revision}`, { method: 'DELETE' }))
    }),
    importConflict: (receipt: Receipt) => action(async () => {
      if (dirty || remote || active) throw new Error('Save or resolve all drafts and wait for execution before importing historical output.')
      if (receipt.state !== 'conflict' || !receipt.result?.outputs) throw new Error('This receipt has no recoverable conflict output.')
      const before = current.current!
      if (receipt.notebook_id !== before.metadata.europa.notebook_id) throw new Error('Receipt belongs to another notebook.')
      const cell = before.cells.find(c => c.id === receipt.cell_id)
      if (!cell || cell.cell_type !== 'code') throw new Error('The original code cell no longer exists. Receipt remains available for inspection.')
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source(cell)))
      const fingerprint = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
      if (fingerprint !== receipt.source_hash) throw new Error('Source has changed. Historical output cannot be attached to different code; review and rerun instead.')
      const document = { ...before, cells: before.cells.map(c => c.id === cell.id ? { ...c, outputs: receipt.result!.outputs, execution_count: receipt.result!.execution_count,
        metadata: { ...c.metadata, europa_recovered_output: receipt.execution_id } } : c) }
      docUpdate(await request<Notebook>(`/notebooks/${id}`, json('PUT', { revision: before.metadata.europa.revision, document })))
      setError('Historical output copied with provenance. The receipt remains conflict, not applied; this does not establish fresh execution success.')
    }),
  }
}
