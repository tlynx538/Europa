import React, { useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import Markdown from 'react-markdown'
import { python } from '@codemirror/lang-python'
import { ApiError, json, request, type Receipt } from './api'
import { Outputs, type Output } from './Output'
import { Workspace } from './Workspace'
import './style.css'

type Meta = { notebook_id: string; title: string; revision: number }
type Cell = { id: string; cell_type: 'code' | 'markdown'; source: string | string[]; metadata: { europa?: { execution_id: string; status: string } }; outputs?: Output[] }
type Notebook = { metadata: { europa: Meta }; cells: Cell[] }
const source = (cell: Cell) => Array.isArray(cell.source) ? cell.source.join('') : cell.source

function App() {
  const [list, setList] = useState<Meta[]>([])
  const [doc, setDoc] = useState<Notebook | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [remote, setRemote] = useState<Notebook | null>(null)
  const [receipts, setReceipts] = useState<Record<string, Receipt>>({})
  const [commentCounts, setCommentCounts] = useState<Record<string, number>>({})
  const [targetCell, setTargetCell] = useState<string | null>(null)
  const updateCommentCounts = useCallback((counts: Record<string, number>) => setCommentCounts(counts), [])
  const id = doc?.metadata.europa.notebook_id
  useEffect(() => {
    if (!targetCell || !doc) return
    const target = document.getElementById(`cell-${targetCell}`)
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTargetCell(null)
  }, [doc, targetCell])
  const refreshList = () => request<Meta[]>('/notebooks').then(setList).catch(e => setError(String(e)))
  useEffect(() => { void refreshList() }, [])
  async function open(notebook: string) {
    const result = await request<Notebook>(`/notebooks/${notebook}`)
    setDoc(result); setDrafts({}); setReceipts({}); setRemote(null); setError('')
  }
  const dirty = !!doc && doc.cells.some(c => drafts[c.id] !== undefined && drafts[c.id] !== source(c))
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('')
    try { await fn() } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? 'Revision conflict or notebook busy. Your drafts are retained. Compare with the saved notebook before retrying.' : String(e))
    } finally { setBusy(false) }
  }
  async function mutate(path: string, body: object, method = 'POST') {
    if (!doc || dirty) throw new Error('Save or discard drafts before changing the notebook.')
    const next = await request<Notebook>(`/notebooks/${id}${path}`, json(method, { revision: doc.metadata.europa.revision, ...body }))
    setDoc(next)
  }
  useEffect(() => {
    if (!id) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    async function recover() {
      try {
        const latest: Record<string, Receipt> = {}
        let offset: number | null = 0
        do {
          const page: { items: Receipt[]; next_offset: number | null } = await request(`/notebooks/${id}/executions?offset=${offset}&limit=100`)
          for (const receipt of page.items) latest[receipt.cell_id] = receipt
          offset = page.next_offset
        } while (offset !== null && !stopped)
        if (!stopped) setReceipts(latest)
      } catch {
        if (!stopped) setError('Receipt connection lost. Execution may still be running; reconnecting automatically.')
      } finally { if (!stopped) timer = setTimeout(recover, 3000) }
    }
    void recover()
    return () => { stopped = true; clearTimeout(timer) }
  }, [id])
  return <div className="shell">
    <aside className="notebook-list"><h1>Europa</h1><h2>Notebooks</h2><button disabled={busy || dirty} onClick={() => void action(async () => {
      const title = prompt('Notebook title'); if (!title) return
      const next = await request<Notebook>('/notebooks', json('POST', { title })); setDoc(next); setDrafts({}); await refreshList()
    })}>New notebook</button>{list.map(n => <button className="list" key={n.notebook_id} disabled={busy || dirty} onClick={() => void action(() => open(n.notebook_id))}>{n.title}</button>)}<footer>Document storage is separate from live kernel state.</footer></aside>
    <main><header><h2>{doc?.metadata.europa.title ?? 'Open a notebook'}</h2>{doc && <button disabled={busy} onClick={() => { if (!dirty || confirm('Discard unsaved drafts and reload?')) void action(() => open(id!)) }}>Reload saved notebook</button>}</header>
      {error && <div role="alert" className="alert">{error}</div>}
      {doc && dirty && <button disabled={busy} onClick={() => void action(async () => setRemote(await request<Notebook>(`/notebooks/${id}`)))}>Compare with saved notebook</button>}
      {remote && doc && <section className="conflict"><h3>Compare before resolving</h3><p>Saved revision {remote.metadata.europa.revision}. Reapplying a draft overwrites that cell’s saved source only after you explicitly save it.</p>
        {doc.cells.filter(c => drafts[c.id] !== undefined && drafts[c.id] !== source(c)).map(c => <div key={c.id}><h4>Cell {c.id}</h4><strong>Saved source</strong><pre>{remote.cells.find(r => r.id === c.id) ? source(remote.cells.find(r => r.id === c.id)!) : '(cell deleted on server)'}</pre><strong>Your draft</strong><pre>{drafts[c.id]}</pre></div>)}
        <button disabled={busy || doc.cells.some(c => drafts[c.id] !== undefined && drafts[c.id] !== source(c) && !remote.cells.some(r => r.id === c.id))} onClick={() => { setDoc(remote); setRemote(null); setError('Drafts retained against the newer revision. Review and save each cell explicitly.') }}>Keep drafts for review</button>{' '}
        <button disabled={busy} onClick={() => { setDoc(remote); setDrafts({}); setRemote(null); setError('') }}>Discard drafts and use saved version</button>
        <p>Drafts for cells deleted on the server cannot be reapplied here; copy them before discarding.</p>
      </section>}
      {doc && <><p>Revision {doc.metadata.europa.revision} · {dirty ? 'Unsaved edits' : 'Saved document'}</p>
      {doc.cells.map((cell, index) => <section className="cell" id={`cell-${cell.id}`} key={cell.id}>
        <div className="toolbar"><strong>{cell.cell_type}</strong>{commentCounts[cell.id] ? <a href="#discussion">{commentCounts[cell.id]} comments</a> : null}<span>{receipts[cell.id] ? `Execution: ${receipts[cell.id].state}${receipts[cell.id].result ? ` (${receipts[cell.id].result!.status})` : ''}` : cell.metadata.europa?.status ?? ''}</span>
          <button disabled={busy || drafts[cell.id] === undefined || drafts[cell.id] === source(cell)} onClick={() => void action(async () => {
            const next = await request<Notebook>(`/notebooks/${id}/cells/${cell.id}`, json('PATCH', { revision: doc.metadata.europa.revision, source: drafts[cell.id] }))
            setDoc(next); setDrafts(prev => { const copy = { ...prev }; delete copy[cell.id]; return copy })
          })}>Save cell</button>
          {cell.cell_type === 'code' && <button disabled={busy || dirty} onClick={() => void action(async () => {
            let receipt: Receipt
            try { receipt = await request<Receipt>(`/notebooks/${id}/cells/${cell.id}/execute`, json('POST', { revision: doc.metadata.europa.revision })) }
            catch (e) {
              if (e instanceof ApiError) throw e
              throw new Error('Execution submission response was lost. It may have been accepted; do not retry until receipt history and the saved notebook have been checked.')
            }
            setReceipts(prev => ({ ...prev, [cell.id]: receipt }))
            try { await open(id!) } catch {
              throw new Error(`Execution ${receipt.execution_id} was accepted, but reconnect failed. It may still be running; reopen the notebook to reconnect.`)
            }
          })}>Run</button>}
          <button disabled={busy || dirty || index === 0} onClick={() => void action(() => mutate(`/cells/${cell.id}/move`, { index: index - 1 }))}>↑</button>
          <button disabled={busy || dirty || index === doc.cells.length - 1} onClick={() => void action(() => mutate(`/cells/${cell.id}/move`, { index: index + 1 }))}>↓</button>
          <button disabled={busy || dirty} onClick={() => { if (confirm('Delete this cell?')) void action(async () => setDoc(await request<Notebook>(`/notebooks/${id}/cells/${cell.id}?revision=${doc.metadata.europa.revision}`, { method: 'DELETE' }))) }}>Delete</button></div>
        <CodeMirror value={drafts[cell.id] ?? source(cell)} extensions={cell.cell_type === 'code' ? [python()] : []} editable={!busy} onChange={value => setDrafts(prev => ({ ...prev, [cell.id]: value }))} />
        {cell.cell_type === 'markdown' && <div className="markdown-preview"><Markdown skipHtml components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>[Image: {alt ?? ''}]</span> }}>{drafts[cell.id] ?? source(cell)}</Markdown></div>}
        <Outputs outputs={cell.outputs ?? []} />
      </section>)}<div><button disabled={busy || dirty} onClick={() => void action(() => mutate('/cells', { cell_type: 'code' }))}>+ Code</button> <button disabled={busy || dirty} onClick={() => void action(() => mutate('/cells', { cell_type: 'markdown' }))}>+ Markdown</button></div></>}
    </main><Workspace notebookId={id} cells={doc?.cells ?? []} onCommentCounts={updateCommentCounts} onOpenNotebook={(notebook, cell) => {
      if (!busy && (!dirty || confirm('Discard unsaved drafts and open another notebook?'))) void action(async () => { await open(notebook); if (cell) setTargetCell(cell) })
    }} />
  </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
