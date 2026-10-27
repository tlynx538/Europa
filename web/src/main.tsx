import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import { ApiError, json, pollReceipt, request, type Receipt } from './api'
import { Outputs, type Output } from './Output'
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
  const [receipts, setReceipts] = useState<Record<string, Receipt>>({})
  const id = doc?.metadata.europa.notebook_id
  const refreshList = () => request<Meta[]>('/notebooks').then(setList).catch(e => setError(String(e)))
  useEffect(() => { void refreshList() }, [])
  async function open(notebook: string) {
    const result = await request<Notebook>(`/notebooks/${notebook}`)
    setDoc(result); setDrafts({}); setReceipts({}); setError('')
  }
  const dirty = !!doc && doc.cells.some(c => drafts[c.id] !== undefined && drafts[c.id] !== source(c))
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('')
    try { await fn() } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? 'Revision conflict or notebook busy. Your drafts are retained. Copy them before reopening the notebook.' : String(e))
    } finally { setBusy(false) }
  }
  async function mutate(path: string, body: object, method = 'POST') {
    if (!doc || dirty) throw new Error('Save or discard drafts before changing the notebook.')
    const next = await request<Notebook>(`/notebooks/${id}${path}`, json(method, { revision: doc.metadata.europa.revision, ...body }))
    setDoc(next)
  }
  function watch(notebook: string, execution: string) {
    const controller = new AbortController()
    void pollReceipt(notebook, execution, controller.signal, receipt => {
      setReceipts(prev => ({ ...prev, [receipt.cell_id]: receipt }))
      // The user explicitly reloads: automatic replacement could discard local edits.
    }).catch(e => { if (!controller.signal.aborted) setError(`Receipt polling disconnected: ${String(e)}. Execution may still be running; reopen to reconnect.`) })
    return () => controller.abort()
  }
  useEffect(() => {
    if (!doc || !id) return
    const cancel = doc.cells.filter(c => c.metadata.europa?.status === 'running' || c.metadata.europa?.status === 'uncertain')
      .map(c => watch(id, c.metadata.europa!.execution_id))
    return () => cancel.forEach(fn => fn())
  }, [doc, id])
  return <div className="shell">
    <aside><h1>Europa</h1><h2>Notebooks</h2><button disabled={busy || dirty} onClick={() => void action(async () => {
      const title = prompt('Notebook title'); if (!title) return
      const next = await request<Notebook>('/notebooks', json('POST', { title })); setDoc(next); setDrafts({}); await refreshList()
    })}>New notebook</button>{list.map(n => <button className="list" key={n.notebook_id} disabled={busy || dirty} onClick={() => void action(() => open(n.notebook_id))}>{n.title}</button>)}<p className="muted">Roadmap and project controls are not yet available.</p><footer>Document storage is separate from live kernel state.</footer></aside>
    <main><header><h2>{doc?.metadata.europa.title ?? 'Open a notebook'}</h2>{doc && <button disabled={busy} onClick={() => { if (!dirty || confirm('Discard unsaved drafts and reload?')) void action(() => open(id!)) }}>Reload saved notebook</button>}</header>
      {error && <div role="alert" className="alert">{error}</div>}
      {doc && <><p>Revision {doc.metadata.europa.revision} · {dirty ? 'Unsaved edits' : 'Saved document'}</p>
      {doc.cells.map((cell, index) => <section className="cell" id={`cell-${cell.id}`} key={cell.id}>
        <div className="toolbar"><strong>{cell.cell_type}</strong><span>{receipts[cell.id] ? `Execution: ${receipts[cell.id].state}${receipts[cell.id].result ? ` (${receipts[cell.id].result!.status})` : ''}` : cell.metadata.europa?.status ?? ''}</span>
          <button disabled={busy || drafts[cell.id] === undefined || drafts[cell.id] === source(cell)} onClick={() => void action(async () => {
            const next = await request<Notebook>(`/notebooks/${id}/cells/${cell.id}`, json('PATCH', { revision: doc.metadata.europa.revision, source: drafts[cell.id] }))
            setDoc(next); setDrafts(prev => { const copy = { ...prev }; delete copy[cell.id]; return copy })
          })}>Save cell</button>
          {cell.cell_type === 'code' && <button disabled={busy || dirty} onClick={() => void action(async () => {
            const receipt = await request<Receipt>(`/notebooks/${id}/cells/${cell.id}/execute`, json('POST', { revision: doc.metadata.europa.revision }))
            setReceipts(prev => ({ ...prev, [cell.id]: receipt }))
            try { await open(id!) } catch {
              throw new Error(`Execution ${receipt.execution_id} was accepted, but reconnect failed. It may still be running; reopen the notebook to reconnect.`)
            }
          })}>Run</button>}
          <button disabled={busy || dirty || index === 0} onClick={() => void action(() => mutate(`/cells/${cell.id}/move`, { index: index - 1 }))}>↑</button>
          <button disabled={busy || dirty || index === doc.cells.length - 1} onClick={() => void action(() => mutate(`/cells/${cell.id}/move`, { index: index + 1 }))}>↓</button>
          <button disabled={busy || dirty} onClick={() => { if (confirm('Delete this cell?')) void action(async () => setDoc(await request<Notebook>(`/notebooks/${id}/cells/${cell.id}?revision=${doc.metadata.europa.revision}`, { method: 'DELETE' }))) }}>Delete</button></div>
        <CodeMirror value={drafts[cell.id] ?? source(cell)} extensions={cell.cell_type === 'code' ? [python()] : []} editable={!busy} onChange={value => setDrafts(prev => ({ ...prev, [cell.id]: value }))} />
        <Outputs outputs={cell.outputs ?? []} />
      </section>)}<div><button disabled={busy || dirty} onClick={() => void action(() => mutate('/cells', { cell_type: 'code' }))}>+ Code</button> <button disabled={busy || dirty} onClick={() => void action(() => mutate('/cells', { cell_type: 'markdown' }))}>+ Markdown</button></div></>}
    </main><aside><h2>Discussion</h2><p>Conversation and comments are not yet available in this preview.</p><p>Uploads are not yet available.</p><footer>Coordinator controls are not implemented.</footer></aside>
  </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
