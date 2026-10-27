import React, { useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { keymap } from '@codemirror/view'
import { Prec } from '@codemirror/state'
import Markdown from 'react-markdown'
import { python } from '@codemirror/lang-python'
import { request, type Receipt } from './api'
import { Outputs } from './Output'
import { Workspace } from './Workspace'
import { CommentIndicator, Layout } from './Layout'
import { source, useNotebook, type Meta } from './useNotebook'
import './style.css'

function App() {
  const nb = useNotebook()
  const { doc, drafts, busy, error, remote, dirty, receipts } = nb
  const id = doc?.metadata.europa.notebook_id
  const [list, setList] = useState<Meta[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [selectedCell, setSelectedCell] = useState<string | undefined>()
  const [activity, setActivity] = useState(false)
  const [conflictResult, setConflictResult] = useState<Receipt | null>(null)
  const [preview, setPreview] = useState<Record<string, boolean>>({})
  const updateCounts = useCallback((value: Record<string, number>) => setCounts(value), [])
  useEffect(() => { setConflictResult(null); setSelectedCell(previous => doc?.cells.some(c => c.id === previous) ? previous : undefined) }, [id])
  useEffect(() => { void request<Meta[]>('/notebooks').then(setList).catch(e => nb.setError(String(e))) }, [id, doc?.metadata.europa.title])
  useEffect(() => {
    if (selectedCell) document.getElementById(`cell-${selectedCell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [selectedCell, id])
  const latest = Object.fromEntries(receipts.map(r => [r.cell_id, r]))
  return <Layout><main>
    <header><h1>{doc?.metadata.europa.title ?? 'Notebook'}</h1><select aria-label="Notebook" value={id ?? ''} disabled={busy} onChange={e => { if (e.target.value) void nb.open(e.target.value) }}><option value="">Open notebook</option>{list.map(n => <option key={n.notebook_id} value={n.notebook_id}>{n.title}</option>)}</select>
      <button disabled={busy || dirty} onClick={() => { const title = prompt('Notebook title')?.trim(); if (title) void nb.create(title) }}>New notebook</button>
    </header>
    {doc && <div className="toolbar">
      <button disabled={busy || dirty} onClick={() => { const title = prompt('Notebook title', doc.metadata.europa.title)?.trim(); if (title) void nb.mutate('', { title }, 'PATCH') }}>Rename</button>
      <button disabled={busy} onClick={() => { if (!dirty || confirm('Discard browser drafts and reload saved notebook?')) void nb.open(id!, true) }}>Reload saved notebook</button>
      <label><input type="checkbox" checked={nb.autosave} onChange={e => nb.setAutosave(e.target.checked)} />Autosave</label>
      <button disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}>Save all</button>
      <button aria-pressed={activity} onClick={() => setActivity(!activity)}>{activity ? 'Notebook only' : 'Contextual activity'}</button>
      <span>Revision {doc.metadata.europa.revision}</span>
    </div>}
    {error && <p role="alert" className="alert">{error}</p>}
    {nb.paused && !remote && <button disabled={busy} onClick={nb.resume}>Resume autosave after review</button>}
    {doc && dirty && <button disabled={busy} onClick={() => void nb.compare()}>Compare with saved notebook</button>}
    {remote && <section className="conflict"><h2>Compare before resolving</h2><p>Server revision {remote.metadata.europa.revision}. Reapplying drafts requires explicit review and a new revision-checked save.</p>
      {Object.entries(drafts).map(([cell, value]) => <div key={cell}><h3>Cell {cell}</h3><strong>Saved source</strong><pre>{remote.cells.some(c => c.id === cell) ? source(remote.cells.find(c => c.id === cell)!) : '(deleted on server)'}</pre><strong>Your draft</strong><pre>{value}</pre></div>)}
      <button disabled={Object.keys(drafts).some(cell => !remote.cells.some(c => c.id === cell))} onClick={() => nb.resolve(true)}>Keep drafts for review</button>{' '}
      <button onClick={() => nb.resolve(false)}>Discard drafts and use saved version</button><p>Copy drafts of deleted cells before discarding them.</p>
    </section>}
    {doc?.cells.map((cell, index) => <section className={`cell ${selectedCell === cell.id ? 'selected' : ''}`} id={`cell-${cell.id}`} key={cell.id} onFocus={() => setSelectedCell(cell.id)}>
      <div className="toolbar"><strong>{cell.cell_type} {cell.cell_type === 'code' ? `[${cell.execution_count ?? ' '}]` : ''}</strong><small>Cell {cell.id}</small>
        {counts[cell.id] ? <CommentIndicator cell={cell.id} count={counts[cell.id]} /> : null}
        <span>{latest[cell.id] ? `${latest[cell.id].state === 'applied' && cell.metadata.europa?.execution_id !== latest[cell.id].execution_id ? 'Previous execution' : 'Execution'}: ${latest[cell.id].state}${latest[cell.id].result ? ` (${latest[cell.id].result!.status})` : ''}${drafts[cell.id] !== undefined ? ' · draft not executed' : ''}` : cell.metadata.europa?.status ?? ''}</span>
        <button disabled={busy || drafts[cell.id] === undefined || !!remote} onClick={() => void nb.save()}>Save cell</button>
        {cell.cell_type === 'code' && <button disabled={busy || nb.active || nb.paused} onClick={() => void nb.execute(cell.id)}>Run</button>}
        {latest[cell.id]?.state === 'running' && <button disabled={busy} onClick={() => void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[cell.id].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') })}>Interrupt</button>}
        {cell.cell_type === 'markdown' && <button onClick={() => setPreview(p => ({ ...p, [cell.id]: p[cell.id] === false }))}>{preview[cell.id] === false ? 'Preview' : 'Hide preview'}</button>}
        <button aria-label={`Move cell ${index + 1} up`} disabled={busy || nb.active || index === 0} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index - 1 })}>↑</button>
        <button aria-label={`Move cell ${index + 1} down`} disabled={busy || nb.active || index === doc.cells.length - 1} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index + 1 })}>↓</button>
        <button disabled={busy || nb.active} onClick={() => { if (confirm('Delete this cell?')) void nb.remove(cell.id) }}>Delete</button>
      </div>
      <CodeMirror value={drafts[cell.id] ?? source(cell)} extensions={[
        ...(cell.cell_type === 'code' ? [python()] : []),
        Prec.highest(keymap.of([{ key: 'Shift-Enter', run: () => { if (cell.cell_type === 'code' && !nb.active && !busy) void nb.execute(cell.id); return true } },
          { key: 'Mod-s', run: () => { void nb.save(); return true } }, { key: 'Escape', run: view => { view.contentDOM.blur(); return true } }])),
      ]} editable={!busy} onChange={value => nb.edit(cell.id, value)} />
      {cell.cell_type === 'markdown' && preview[cell.id] !== false && <div className="markdown-preview"><Markdown skipHtml components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>[Image: {alt ?? ''}]</span> }}>{drafts[cell.id] ?? source(cell)}</Markdown></div>}
      <Outputs outputs={cell.outputs ?? []} />
      {!cell.metadata.europa && typeof cell.metadata.europa_recovered_output === 'string' && <p className="muted">Historical output copied from conflict receipt {cell.metadata.europa_recovered_output}. Not a fresh execution result.</p>}
    </section>)}
    {doc && <div className="toolbar"><button disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'code' })}>+ Code</button><button disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'markdown' })}>+ Markdown</button></div>}
    {receipts.length > 0 && <details className="receipt-history"><summary>Execution history ({receipts.length})</summary>{receipts.map(r => <div key={r.execution_id}><code>{r.execution_id.slice(0, 10)}</code> · {r.state} {r.result?.status}
      {r.state === 'conflict' && <button onClick={() => void nb.action(async () => setConflictResult(await request<Receipt>(`/notebooks/${id}/executions/${r.execution_id}`)))}>Inspect conflict outputs</button>}</div>)}</details>}
    {conflictResult && <section className="conflict"><h2>Conflict output — not applied</h2><p>Receipt {conflictResult.execution_id}. Newer edits are unchanged. You may explicitly copy historical output only if the source still matches. The receipt will remain conflict, never execution evidence of success. Re-run reviewed code for a fresh result.</p><Outputs outputs={conflictResult.result?.outputs ?? []} />
      <button disabled={busy || dirty || nb.active} onClick={() => { if (confirm('Replace this cell’s saved outputs with historical conflict output? Source must match and the current revision must still be valid.')) void nb.importConflict(conflictResult) }}>Copy historical output to unchanged cell</button>{' '}
      <button onClick={() => setConflictResult(null)}>Dismiss preview (receipt retained)</button></section>}
    <footer className="status" role="status"><span>API: {nb.connection}</span><span>Kernel: {nb.session.state} ({nb.session.kernels})</span><span>{busy ? 'Saving / requesting…' : nb.paused ? 'Autosave paused' : dirty ? 'Unsaved draft (browser recovery)' : doc ? 'Document saved' : 'No notebook selected'}</span><span>{nb.active ? 'Runtime executing' : 'No pending receipt'}</span><span>Agents: not implemented</span><small>Saved outputs do not checkpoint Python variables.</small></footer>
  </main><Workspace notebookId={id} revision={doc?.metadata.europa.revision} cells={doc?.cells ?? []} selectedCell={selectedCell} showActivity={activity} onCommentCounts={updateCounts} onOpenNotebook={(notebook, cell) => { if (notebook === id) { if (cell) { setSelectedCell(cell); document.getElementById(`cell-${cell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) } } else void nb.open(notebook).then(() => { if (cell) setSelectedCell(cell) }) }} /></Layout>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
