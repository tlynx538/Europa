import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { keymap } from '@codemirror/view'
import { Prec } from '@codemirror/state'
import Markdown from 'react-markdown'
import { python } from '@codemirror/lang-python'
import { request, type Receipt } from './api'
import { Outputs } from './Output'
import { Workspace } from './Workspace'
import { CommentIndicator, HeaderSlot, Layout, StatusSlot, usePanels } from './Layout'
import { source, useNotebook } from './useNotebook'
import { Navigation, type Navigate } from './Navigation'
import './style.css'

function ViewPanels() {
  const panels = usePanels()
  return <><button aria-pressed={panels.left} onClick={() => panels.setLeft(!panels.left)}>Roadmap panel</button><button aria-pressed={panels.right} onClick={() => panels.setRight(!panels.right)}>Discussion panel</button></>
}

function App() {
  const nb = useNotebook()
  const { doc, drafts, busy, error, remote, dirty, receipts } = nb
  const id = doc?.metadata.europa.notebook_id
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [selectedCell, setSelectedCell] = useState<string | undefined>()
  const pendingCell = useRef<{ notebook: string; cell: string } | null>(null)
  const deleteArmed = useRef(0)
  const [activity, setActivity] = useState(false)
  const [conflictResult, setConflictResult] = useState<Receipt | null>(null)
  const [preview, setPreview] = useState<Record<string, boolean>>({})
  const [mode, setMode] = useState<'command' | 'edit'>('command')
  const [clipboard, setClipboard] = useState<{ cell_type: 'code' | 'markdown'; source: string } | null>(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('europa.theme') ?? 'light')
  const copy = () => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell) setClipboard({ cell_type: cell.cell_type, source: drafts[cell.id] ?? source(cell) }) }
  const paste = () => { if (clipboard) void nb.paste(clipboard, selectedCell ? (doc?.cells.findIndex(c => c.id === selectedCell) ?? -1) + 1 : doc?.cells.length ?? 0) }
  useEffect(() => {
    const command = (event: KeyboardEvent) => {
      if (!location.pathname.startsWith('/notebooks/') || event.altKey || event.ctrlKey || event.metaKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || (event.target instanceof HTMLElement && (event.target.isContentEditable || !!event.target.closest('.cm-editor, button, summary, dialog')))) return
      if (mode !== 'command' || !selectedCell || !doc || busy || nb.active) return
      const index = doc.cells.findIndex(c => c.id === selectedCell)
      if (event.key === 'j' || event.key === 'ArrowDown') setSelectedCell(doc.cells[index + 1]?.id ?? selectedCell)
      else if (event.key === 'k' || event.key === 'ArrowUp') setSelectedCell(doc.cells[index - 1]?.id ?? selectedCell)
      else if (event.key === 'c') copy()
      else if (event.key === 'v') paste()
      else if (event.key === 'x' && !dirty && confirm('Cut selected cell?')) { const cell = doc.cells[index]; setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) }
      else if (event.key === 'd') { if (!dirty && Date.now() - deleteArmed.current < 800 && confirm('Delete selected cell?')) void nb.remove(selectedCell); deleteArmed.current = Date.now() }
      else if (event.key === 'Enter') { document.querySelector<HTMLElement>(`#cell-${CSS.escape(selectedCell)} .cm-content`)?.focus(); setMode('edit') }
      else if (event.key === 'Escape') { (document.activeElement as HTMLElement)?.blur(); setMode('command') }
      else if (event.key === 'a' || event.key === 'b') void nb.mutate('/cells', { cell_type: 'code', index: index + (event.key === 'b' ? 1 : 0) })
      else if (event.key === 'm' || event.key === 'y') { if (doc.cells[index].cell_type !== (event.key === 'm' ? 'markdown' : 'code') && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${selectedCell}/type`, { cell_type: event.key === 'm' ? 'markdown' : 'code' }) }
      else return
      event.preventDefault()
    }
    document.addEventListener('keydown', command)
    return () => document.removeEventListener('keydown', command)
  })
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('europa.theme', theme) }, [theme])
  const updateCounts = useCallback((value: Record<string, number>) => setCounts(value), [])
  useEffect(() => { setConflictResult(null); const pending = pendingCell.current; setSelectedCell(pending && pending.notebook === id && doc?.cells.some(c => c.id === pending.cell) ? pending.cell : undefined); pendingCell.current = null; setMode('command') }, [id])
  useEffect(() => {
    if (selectedCell) document.getElementById(`cell-${selectedCell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [selectedCell, id])
  const latest = Object.fromEntries(receipts.map(r => [r.cell_id, r]))
  const openNotebook = useCallback(async (target: string) => target === nb.doc?.metadata.europa.notebook_id ? true : !!await nb.open(target), [nb])
  const editor = (routeId: string, navigate: Navigate) => <Layout><HeaderSlot><span className="save-indicator">{dirty ? 'Unsaved draft' : doc ? 'Saved' : ''}</span><details className="appbar-more"><summary>File</summary><div>
    <button onClick={() => void navigate('/notebooks')}>Notebooks</button><button onClick={() => void navigate('/notebooks')}>New notebook…</button><button disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}>Save</button>
    {doc && <><button disabled={busy || dirty} onClick={() => { const title = prompt('Notebook title', doc.metadata.europa.title)?.trim(); if (title) void nb.mutate('', { title }, 'PATCH') }}>Rename</button><button disabled={busy} onClick={() => { if (!dirty || confirm('Discard browser drafts and reload saved notebook?')) void nb.open(id!, true) }}>Reload saved notebook</button><button aria-pressed={activity} onClick={() => setActivity(!activity)}>{activity ? 'Notebook only' : 'Contextual activity'}</button></>}
  </div></details><details className="appbar-more"><summary>Edit</summary><div>
    <button disabled={!selectedCell || busy || nb.active} onClick={copy}>Copy cell</button>
    <button disabled={!selectedCell || busy || nb.active || dirty} aria-describedby={dirty ? 'cut-disabled-reason' : undefined} onClick={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && confirm('Cut selected cell?')) { setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) } }}>Cut cell</button>
    {dirty && <p id="cut-disabled-reason">Save all drafts before cutting a cell. Copy and paste remain available.</p>}
    <button disabled={!clipboard || busy || nb.active} onClick={paste}>Paste cell</button>
    <button disabled={!selectedCell || busy || nb.active} onClick={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${cell.id}/type`, { cell_type: cell.cell_type === 'code' ? 'markdown' : 'code' }) }}>Toggle cell type</button>
    <button disabled={!selectedCell || busy || nb.active || dirty} onClick={() => { if (selectedCell && confirm('Delete selected cell?')) void nb.remove(selectedCell) }}>Delete cell</button>
  </div></details><details className="appbar-more"><summary>View</summary><div><label>Theme <select aria-label="Theme" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><ViewPanels /><button onClick={() => document.querySelector('.receipt-history')?.scrollIntoView()}>Execution history</button><button onClick={() => setActivity(!activity)}>Contextual activity</button></div></details>
    <details className="appbar-more"><summary>Run</summary><div><button disabled={!selectedCell || busy || nb.active || nb.paused} onClick={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell?.cell_type === 'code') void nb.execute(cell.id) }}>Run selected cell</button><button disabled={busy || nb.active || nb.paused} onClick={() => void nb.runAll()}>Run all (sequential)</button></div></details>
    <details className="appbar-more"><summary>Kernel</summary><div><p id="kernel-disabled-reason">Kernel restart and shutdown are unavailable: no verified notebook-scoped control contract exists. Saved outputs do not preserve interpreter state.</p><button disabled aria-describedby="kernel-disabled-reason">Restart kernel</button><button disabled aria-describedby="kernel-disabled-reason">Shut down kernel</button></div></details>
    <details className="appbar-more"><summary>Help</summary><div><p>In the editor, Shift+Enter runs a code cell and selects the next cell; Ctrl/Cmd+S saves drafts. Escape leaves edit mode. In command mode: J/K or arrows select cells, Enter edits, A/B inserts above/below, C copies, V pastes, X cuts saved cells, D D deletes saved cells, M/Y changes cell type. Receipt history records execution outcomes.</p></div></details></HeaderSlot><main>
    {id !== routeId && <p role="status">Opening notebook…</p>}
    {id === routeId && <>
    <header><h1>{doc?.metadata.europa.title ?? 'Notebook'}</h1>
    </header>
    {doc && <div className="toolbar editor-tools" role="toolbar" aria-label="Notebook commands"><span>Mode: {mode}</span>
      <button aria-label="Add code cell" title="Add code cell" disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'code' })}>+ Code</button>
      <button aria-label="Add markdown cell" title="Add markdown cell" disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'markdown' })}>+ Markdown</button>
      <button aria-label="Insert above" title="Insert above" disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'code', index: selectedCell ? doc.cells.findIndex(c => c.id === selectedCell) : doc.cells.length })}>↑+</button>
      <button aria-label="Insert below" title="Insert below" disabled={busy || nb.active} onClick={() => void nb.mutate('/cells', { cell_type: 'code', index: selectedCell ? doc.cells.findIndex(c => c.id === selectedCell) + 1 : doc.cells.length })}>↓+</button>
      <button aria-label="Move up" title="Move up" disabled={busy || nb.active || !selectedCell || doc.cells[0]?.id === selectedCell} onClick={() => { const index = doc.cells.findIndex(c => c.id === selectedCell); if (index > 0) void nb.mutate(`/cells/${selectedCell}/move`, { index: index - 1 }) }}>↑</button>
      <button aria-label="Move down" title="Move down" disabled={busy || nb.active || !selectedCell || doc.cells.at(-1)?.id === selectedCell} onClick={() => { const index = doc.cells.findIndex(c => c.id === selectedCell); if (index < doc.cells.length - 1) void nb.mutate(`/cells/${selectedCell}/move`, { index: index + 1 }) }}>↓</button>
      <button aria-label="Copy cell" title="Copy cell" disabled={!selectedCell || busy} onClick={copy}>Copy</button>
      <button aria-label="Cut cell" title={dirty ? 'Save all drafts before cutting' : 'Cut cell'} aria-describedby={dirty ? 'toolbar-cut-reason' : undefined} disabled={!selectedCell || busy || nb.active || dirty} onClick={() => { const cell = doc.cells.find(c => c.id === selectedCell); if (cell && confirm('Cut selected cell?')) { setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) } }}>Cut</button>
      {dirty && <small id="toolbar-cut-reason">Save drafts before cutting</small>}
      <button aria-label="Paste cell" title="Paste cell" disabled={!clipboard || busy || nb.active} onClick={paste}>Paste</button>
      <button aria-label="Toggle cell type" title="Toggle cell type (clears execution outputs)" disabled={!selectedCell || busy || nb.active} onClick={() => { const cell = doc.cells.find(c => c.id === selectedCell); if (cell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${cell.id}/type`, { cell_type: cell.cell_type === 'code' ? 'markdown' : 'code' }) }}>Code/MD</button>
      <button aria-label="Run selected cell" title="Run selected cell" disabled={!selectedCell || doc.cells.find(c => c.id === selectedCell)?.cell_type !== 'code' || busy || nb.active || nb.paused} onClick={() => { if (selectedCell) void nb.execute(selectedCell) }}>Run</button>
      <button disabled={busy || nb.active || nb.paused} onClick={() => void nb.runAll()}>Run all</button>
      {selectedCell && latest[selectedCell]?.state === 'running' && <button aria-label="Interrupt selected execution" disabled={busy} onClick={() => void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[selectedCell].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') })}>Interrupt</button>}
      <label><input type="checkbox" checked={nb.autosave} onChange={e => nb.setAutosave(e.target.checked)} />Autosave</label>
      <button disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}>Save all</button>
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
    {doc?.cells.map((cell, index) => <section className={`cell ${selectedCell === cell.id ? 'selected' : ''}`} id={`cell-${cell.id}`} key={cell.id} onFocus={() => setSelectedCell(cell.id)} onClick={() => setSelectedCell(cell.id)}>
      <div className="toolbar"><strong>{cell.cell_type} {cell.cell_type === 'code' ? `[${cell.execution_count ?? ' '}]` : ''}</strong><small>Cell {cell.id}</small>
        {counts[cell.id] ? <CommentIndicator cell={cell.id} count={counts[cell.id]} /> : null}
        <span>{latest[cell.id] ? `${latest[cell.id].state === 'applied' && cell.metadata.europa?.execution_id !== latest[cell.id].execution_id ? 'Previous execution' : 'Execution'}: ${latest[cell.id].state}${latest[cell.id].result ? ` (${latest[cell.id].result!.status})` : ''}${drafts[cell.id] !== undefined ? ' · draft not executed' : ''}` : cell.metadata.europa?.status ?? ''}</span>
        <button disabled={busy || drafts[cell.id] === undefined || !!remote} onClick={() => void nb.save()}>Save cell</button>
        {cell.cell_type === 'code' && <button disabled={busy || nb.active || nb.paused} onClick={() => void nb.execute(cell.id)}>Run</button>}
        {latest[cell.id]?.state === 'running' && <button disabled={busy} onClick={() => void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[cell.id].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') })}>Interrupt</button>}
        {cell.cell_type === 'markdown' && <button onClick={() => setPreview(p => ({ ...p, [cell.id]: p[cell.id] === false }))}>{preview[cell.id] === false ? 'Preview' : 'Hide preview'}</button>}
        <details className="cell-actions"><summary>Cell actions</summary><div>
          <button aria-label={`Move cell ${index + 1} up`} disabled={busy || nb.active || index === 0} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index - 1 })}>↑</button>
          <button aria-label={`Move cell ${index + 1} down`} disabled={busy || nb.active || index === doc.cells.length - 1} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index + 1 })}>↓</button>
          <button disabled={busy || nb.active} onClick={() => { if (confirm('Delete this cell?')) void nb.remove(cell.id) }}>Delete</button>
        </div></details>
      </div>
      <CodeMirror value={drafts[cell.id] ?? source(cell)} theme={theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'} onFocus={() => { setSelectedCell(cell.id); setMode('edit') }} onBlur={() => setMode('command')} extensions={[
        ...(cell.cell_type === 'code' ? [python()] : []),
        Prec.highest(keymap.of([{ key: 'Shift-Enter', run: () => { if (cell.cell_type === 'code' && !nb.active && !busy) void nb.execute(cell.id); setSelectedCell(doc.cells[index + 1]?.id ?? cell.id); return true } },
          { key: 'Mod-s', run: () => { void nb.save(); return true } }, { key: 'Escape', run: view => { view.contentDOM.blur(); setMode('command'); return true } }])),
      ]} editable={!busy} onChange={value => nb.edit(cell.id, value)} />
      {cell.cell_type === 'markdown' && preview[cell.id] !== false && <div className="markdown-preview"><Markdown skipHtml components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>[Image: {alt ?? ''}]</span> }}>{drafts[cell.id] ?? source(cell)}</Markdown></div>}
      <Outputs outputs={cell.outputs ?? []} />
      {!cell.metadata.europa && typeof cell.metadata.europa_recovered_output === 'string' && <p className="muted">Historical output copied from conflict receipt {cell.metadata.europa_recovered_output}. Not a fresh execution result.</p>}
    </section>)}
    {receipts.length > 0 && <details className="receipt-history"><summary>Execution history ({receipts.length})</summary>{receipts.map(r => <div key={r.execution_id}><code>{r.execution_id.slice(0, 10)}</code> · {r.state} {r.result?.status}
      {r.state === 'conflict' && <button onClick={() => void nb.action(async () => setConflictResult(await request<Receipt>(`/notebooks/${id}/executions/${r.execution_id}`)))}>Inspect conflict outputs</button>}</div>)}</details>}
    {conflictResult && <section className="conflict"><h2>Conflict output — not applied</h2><p>Receipt {conflictResult.execution_id}. Newer edits are unchanged. You may explicitly copy historical output only if the source still matches. The receipt will remain conflict, never execution evidence of success. Re-run reviewed code for a fresh result.</p><Outputs outputs={conflictResult.result?.outputs ?? []} />
      <button disabled={busy || dirty || nb.active} onClick={() => { if (confirm('Replace this cell’s saved outputs with historical conflict output? Source must match and the current revision must still be valid.')) void nb.importConflict(conflictResult) }}>Copy historical output to unchanged cell</button>{' '}
      <button onClick={() => setConflictResult(null)}>Dismiss preview (receipt retained)</button></section>}
    <StatusSlot><footer className="status" role="status"><details><summary><span>API: {nb.connection}</span><span>Kernel: {nb.session.state} ({nb.session.kernels})</span><span>{busy ? 'Saving / requesting…' : nb.paused ? 'Autosave paused' : dirty ? 'Unsaved draft (browser recovery)' : doc ? 'Document saved' : 'No notebook selected'}</span><span>{nb.active ? 'Pending execution receipt' : 'No pending receipt'}{receipts.some(r => r.state === 'uncertain') ? ' · Uncertain receipt — review history' : ''}</span></summary><div className="status-details"><small>Saved outputs do not checkpoint Python variables. Execution history and receipts remain available in the notebook.</small></div></details></footer></StatusSlot></>}
  </main>{id === routeId && <Workspace notebookId={id} revision={doc?.metadata.europa.revision} cells={doc?.cells ?? []} selectedCell={selectedCell} showActivity={activity} onCommentCounts={updateCounts} onOpenNotebook={async (notebook, cell) => {
    if (notebook !== id && cell) pendingCell.current = { notebook, cell }
    if (!await navigate(`/notebooks/${notebook}`)) { pendingCell.current = null; return false }
    if (notebook === id && cell && doc?.cells.some(c => c.id === cell)) {
      setSelectedCell(cell)
      document.getElementById(`cell-${cell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    if (notebook !== id && cell) requestAnimationFrame(() => document.getElementById(`cell-${cell}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
    return true
  }} />}</Layout>
  return <Navigation openNotebook={openNotebook} renderNotebook={editor} currentNotebookId={id} onNotebookDeleted={nb.forget} beforeLeave={() => {
    if (busy || nb.active) { nb.setError('Notebook operation or execution receipt is still pending. Wait for it to settle before leaving; execution is not cancelled.'); return false }
    if (dirty && !confirm('Keep drafts in this browser and leave the notebook?')) return false
    return nb.close()
  }} />
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
