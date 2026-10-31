import React, { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
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

function NotebookMenu({ name, open, onToggle, children }: { name: string; open: boolean; onToggle: (name: string | null) => void; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null)
  const focusable = ':scope > div button:not(:disabled), :scope > div select:not(:disabled)'
  useLayoutEffect(() => {
    if (!open || !ref.current) return
    const trigger = ref.current.querySelector('summary')!
    const popup = ref.current.querySelector<HTMLElement>(':scope > div')!
    const left = trigger.getBoundingClientRect().left
    popup.style.left = `${Math.max(8 - left, Math.min(0, innerWidth - left - popup.offsetWidth - 8))}px`
    popup.style.right = 'auto'
  }, [open])
  return <details ref={ref} className="appbar-more" open={open} onKeyDown={event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || event.target instanceof HTMLSelectElement) return
    const details = event.currentTarget
    const options = Array.from(details.querySelectorAll<HTMLElement>(focusable))
    if (!options.length) return
    event.preventDefault()
    if (!open) onToggle(name)
    const index = options.indexOf(event.target as HTMLElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : event.key === 'ArrowUp' ? (index + options.length - 1) % options.length : (index + 1) % options.length
    requestAnimationFrame(() => options[next].focus())
  }}><summary onClick={event => { event.preventDefault(); onToggle(open ? null : name) }}>{name}</summary><div>{children}</div></details>
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
  const [mode, setMode] = useState<'command' | 'edit'>('command')
  const [clipboard, setClipboard] = useState<{ cell_type: 'code' | 'markdown'; source: string } | null>(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('europa.theme') ?? 'light')
  const [openMenu, setOpenMenu] = useState<string | null>(null)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const shortcuts = useRef<HTMLDialogElement>(null)
  const [kernelOpen, setKernelOpen] = useState(false)
  const kernelInfo = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (shortcutsOpen) shortcuts.current?.showModal()
    else shortcuts.current?.close()
  }, [shortcutsOpen])
  useEffect(() => {
    if (kernelOpen) kernelInfo.current?.showModal()
    else kernelInfo.current?.close()
  }, [kernelOpen])
  useEffect(() => {
    if (!openMenu) return
    const dismiss = (event: PointerEvent) => { if (!(event.target as Element).closest('.appbar-more')) setOpenMenu(null) }
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !shortcutsOpen) {
        event.preventDefault(); document.querySelector<HTMLElement>(`.appbar-more[open] summary`)?.focus(); setOpenMenu(null)
      }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', keyboard)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', keyboard) }
  }, [openMenu, shortcutsOpen])
  const canRun = !busy && !nb.active && !nb.paused && !remote
  const runnable = (cell?: string) => canRun && !!doc?.cells.some(c => c.id === cell && c.cell_type === 'code')
  const copy = () => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell) setClipboard({ cell_type: cell.cell_type, source: drafts[cell.id] ?? source(cell) }) }
  const paste = () => { if (clipboard) void nb.paste(clipboard, selectedCell ? (doc?.cells.findIndex(c => c.id === selectedCell) ?? -1) + 1 : doc?.cells.length ?? 0) }
  const insert = () => { if (doc) void nb.mutate('/cells', { cell_type: 'code', index: selectedCell ? doc.cells.findIndex(c => c.id === selectedCell) + 1 : doc.cells.length }) }
  useEffect(() => {
    const command = (event: KeyboardEvent) => {
      if (!location.pathname.startsWith('/notebooks/') || event.altKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || (event.target instanceof HTMLElement && (event.target.isContentEditable || !!event.target.closest('.cm-editor, button, summary, dialog')))) return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void nb.save(); return }
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && selectedCell && doc?.cells.find(c => c.id === selectedCell)?.cell_type === 'code') { event.preventDefault(); if (runnable(selectedCell)) void nb.execute(selectedCell); return }
      if (event.metaKey || event.ctrlKey) return
      if (mode !== 'command' || !selectedCell || !doc || busy || nb.active) return
      const index = doc.cells.findIndex(c => c.id === selectedCell)
      if (event.key === 'j' || event.key === 'ArrowDown') setSelectedCell(doc.cells[index + 1]?.id ?? selectedCell)
      else if (event.key === 'k' || event.key === 'ArrowUp') setSelectedCell(doc.cells[index - 1]?.id ?? selectedCell)
      else if (event.key === 'c') copy()
      else if (event.key === 'v') paste()
      else if (event.key === 'x' && !dirty && confirm('Cut selected cell?')) { const cell = doc.cells[index]; setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) }
      else if (event.key === 'd') { if (!dirty && Date.now() - deleteArmed.current < 800 && confirm('Delete selected cell?')) void nb.remove(selectedCell); deleteArmed.current = Date.now() }
      else if (event.key === 'Enter') { if (doc.cells[index].cell_type === 'markdown') setMode('edit'); else document.querySelector<HTMLElement>(`#cell-${CSS.escape(selectedCell)} .cm-content`)?.focus() }
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
  useEffect(() => {
    if (mode !== 'edit' || !selectedCell || doc?.cells.find(cell => cell.id === selectedCell)?.cell_type !== 'markdown') return
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>(`#cell-${CSS.escape(selectedCell)} .cm-content`)?.focus())
    return () => cancelAnimationFrame(frame)
  }, [mode, selectedCell, id])
  const latest = Object.fromEntries(receipts.map(r => [r.cell_id, r]))
  const openNotebook = useCallback(async (target: string) => target === nb.doc?.metadata.europa.notebook_id ? true : !!await nb.open(target), [nb])
  const editor = (routeId: string, navigate: Navigate) => <Layout><HeaderSlot><div className="notebook-chrome-title"><a href="/notebooks" onClick={e => { e.preventDefault(); void navigate('/notebooks') }}>Notebooks</a><h1>{doc?.metadata.europa.title ?? 'Notebook'}</h1><span className="save-indicator">{dirty ? 'Unsaved draft' : doc ? 'Saved' : ''} · Revision {doc?.metadata.europa.revision ?? '—'}</span><a href="/projects" onClick={e => { e.preventDefault(); void navigate('/projects') }}>Projects</a></div><NotebookMenu name="File" open={openMenu === 'File'} onToggle={setOpenMenu}>
    <button onClick={() => void navigate('/notebooks')}>Notebooks</button><button onClick={() => void navigate('/notebooks')}>New notebook…</button><button disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}>Save</button>
    {doc && <><button disabled={busy || dirty} onClick={() => { const title = prompt('Notebook title', doc.metadata.europa.title)?.trim(); if (title) void nb.mutate('', { title }, 'PATCH') }}>Rename</button><button disabled={busy} onClick={() => { if (!dirty || confirm('Discard browser drafts and reload saved notebook?')) void nb.open(id!, true) }}>Reload saved notebook</button></>}
  </NotebookMenu><NotebookMenu name="Edit" open={openMenu === 'Edit'} onToggle={setOpenMenu}>
    <button disabled={!selectedCell || busy || nb.active} onClick={copy}>Copy cell</button>
    <button disabled={!selectedCell || busy || nb.active || dirty} title={dirty ? 'Save all drafts before cutting a cell. Copy and paste remain available.' : 'Cut selected cell'} onClick={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && confirm('Cut selected cell?')) { setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) } }}>Cut cell</button>
    <button disabled={!clipboard || busy || nb.active} onClick={paste}>Paste cell</button>
    <button disabled={!selectedCell || busy || nb.active} onClick={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${cell.id}/type`, { cell_type: cell.cell_type === 'code' ? 'markdown' : 'code' }) }}>Toggle cell type</button>
    <button disabled={!selectedCell || busy || nb.active || dirty} onClick={() => { if (selectedCell && confirm('Delete selected cell?')) void nb.remove(selectedCell) }}>Delete cell</button>
  </NotebookMenu><NotebookMenu name="View" open={openMenu === 'View'} onToggle={setOpenMenu}><label>Theme <select aria-label="Theme" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><label className="toolbar-autosave"><input type="checkbox" checked={nb.autosave} onChange={e => nb.setAutosave(e.target.checked)} />Autosave</label><ViewPanels /><button onClick={() => document.querySelector('.receipt-history')?.scrollIntoView()}>Execution history</button><button onClick={() => setActivity(!activity)}>Contextual activity</button></NotebookMenu>
    <NotebookMenu name="Run" open={openMenu === 'Run'} onToggle={setOpenMenu}><button disabled={!runnable(selectedCell)} onClick={() => { if (selectedCell && runnable(selectedCell)) void nb.execute(selectedCell) }}>Run selected cell</button><button disabled={!canRun} onClick={() => void nb.runAll()}>Run all (sequential)</button></NotebookMenu>
    <NotebookMenu name="Kernel" open={openMenu === 'Kernel'} onToggle={setOpenMenu}><button onClick={() => { setOpenMenu(null); setKernelOpen(true) }}>Kernel information</button></NotebookMenu>
    <NotebookMenu name="Help" open={openMenu === 'Help'} onToggle={setOpenMenu}><button onClick={() => { setOpenMenu(null); setShortcutsOpen(true) }}>Keyboard shortcuts</button></NotebookMenu></HeaderSlot><main className="notebook-editor">
    <dialog ref={shortcuts} className="editor-dialog" aria-label="Keyboard shortcuts" onClose={() => { setShortcutsOpen(false); document.querySelector<HTMLElement>('.appbar-more:last-of-type summary')?.focus() }}><h2>Keyboard shortcuts</h2><p>Shift+Enter runs a code cell and selects the next cell; Ctrl/Cmd+Enter runs without advancing; Ctrl/Cmd+S saves drafts. Escape leaves edit mode. In command mode: J/K or arrows select cells, Enter edits, A/B inserts above/below, C copies, V pastes, X cuts saved cells, D D deletes saved cells, M/Y changes cell type. Receipt history records execution outcomes. Kernel restart is unavailable.</p><button onClick={() => setShortcutsOpen(false)}>Close shortcuts</button></dialog>
    <dialog ref={kernelInfo} className="editor-dialog" aria-label="Kernel information" onClose={() => { setKernelOpen(false); Array.from(document.querySelectorAll<HTMLElement>('.appbar-more summary')).find(element => element.textContent === 'Kernel')?.focus() }}><h2>Kernel information</h2><p>Observed kernel: {nb.session.state} ({nb.session.kernels}). Restart and shutdown are unavailable without a verified notebook-scoped control contract. Saved outputs do not preserve interpreter state.</p><button onClick={() => setKernelOpen(false)}>Close kernel information</button></dialog>
    {id !== routeId && <p role="status">Opening notebook…</p>}
    {id === routeId && <>
    {doc && <div className="toolbar editor-tools" role="toolbar" aria-label="Notebook commands" onMouseDown={event => { if ((event.target as HTMLElement).closest('button')) event.preventDefault() }}><span className="editor-mode">Mode: {mode}</span>
      <button aria-label="Save notebook" title="Save notebook" disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}>▣</button>
      <button aria-label="Insert cell" title="Insert code cell below" disabled={busy || nb.active} onClick={insert}>＋</button>
      <button aria-label="Run selected cell" title="Run selected cell" disabled={!runnable(selectedCell)} onClick={() => { if (selectedCell && runnable(selectedCell)) void nb.execute(selectedCell) }}>▶</button>
      <button aria-label="Interrupt selected execution" title="Interrupt selected execution" disabled={!selectedCell || latest[selectedCell]?.state !== 'running' || busy} onClick={() => { if (selectedCell && latest[selectedCell]?.state === 'running') void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[selectedCell].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') }) }}>■</button>
      <label>Cell type <select aria-label="Cell type" value={doc.cells.find(c => c.id === selectedCell)?.cell_type ?? 'code'} disabled={!selectedCell || busy || nb.active} onChange={e => { if (selectedCell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${selectedCell}/type`, { cell_type: e.target.value }) }}><option value="code">Code</option><option value="markdown">Markdown</option></select></label>
    </div>}
    <div className="notebook-surface">
    {error && <p role="alert" className="alert">{error}</p>}
    {nb.paused && !remote && <button disabled={busy} onClick={nb.resume}>Resume autosave after review</button>}
    {doc && dirty && <button disabled={busy} onClick={() => void nb.compare()}>Compare with saved notebook</button>}
    {remote && <section className="conflict"><h2>Compare before resolving</h2><p>Server revision {remote.metadata.europa.revision}. Reapplying drafts requires explicit review and a new revision-checked save.</p>
      {Object.entries(drafts).map(([cell, value]) => <div key={cell}><h3>Cell {cell}</h3><strong>Saved source</strong><pre>{remote.cells.some(c => c.id === cell) ? source(remote.cells.find(c => c.id === cell)!) : '(deleted on server)'}</pre><strong>Your draft</strong><pre>{value}</pre></div>)}
      <button disabled={Object.keys(drafts).some(cell => !remote.cells.some(c => c.id === cell))} onClick={() => nb.resolve(true)}>Keep drafts for review</button>{' '}
      <button onClick={() => nb.resolve(false)}>Discard drafts and use saved version</button><p>Copy drafts of deleted cells before discarding them.</p>
    </section>}
    {doc?.cells.map((cell, index) => <section className={`cell ${selectedCell === cell.id ? 'selected' : ''}`} id={`cell-${cell.id}`} key={cell.id} tabIndex={0} aria-label={`${cell.cell_type} cell ${index + 1}`} onFocus={() => setSelectedCell(cell.id)} onClick={() => setSelectedCell(cell.id)}>
      <div className="cell-row"><div className="cell-gutter">{cell.cell_type === 'code' ? `In [${cell.execution_count ?? ' '}]:` : ''}</div><div className="cell-body">
      <div className="cell-meta">{counts[cell.id] ? <CommentIndicator cell={cell.id} count={counts[cell.id]} /> : null}
        <span>{latest[cell.id] ? `${latest[cell.id].state === 'applied' && cell.metadata.europa?.execution_id !== latest[cell.id].execution_id ? 'Previous execution' : 'Execution'}: ${latest[cell.id].state}${latest[cell.id].result ? ` (${latest[cell.id].result!.status})` : ''}${drafts[cell.id] !== undefined ? ' · draft not executed' : ''}` : cell.metadata.europa?.status ?? ''}</span>
        <details className="cell-actions"><summary>Cell actions</summary><div>
          <button disabled={busy || drafts[cell.id] === undefined || !!remote} onClick={() => void nb.save()}>Save cell</button>
          {cell.cell_type === 'code' && <button disabled={!runnable(cell.id)} onClick={() => { if (runnable(cell.id)) void nb.execute(cell.id) }}>Run</button>}
          {latest[cell.id]?.state === 'running' && <button disabled={busy} onClick={() => void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[cell.id].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') })}>Interrupt</button>}
          <button aria-label={`Move cell ${index + 1} up`} disabled={busy || nb.active || index === 0} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index - 1 })}>Move up</button>
          <button aria-label={`Move cell ${index + 1} down`} disabled={busy || nb.active || index === doc.cells.length - 1} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index + 1 })}>Move down</button>
          <button disabled={busy || nb.active} onClick={() => { if (confirm('Delete this cell?')) void nb.remove(cell.id) }}>Delete</button>
        </div></details>
      </div>
      {(cell.cell_type === 'code' || (mode === 'edit' && selectedCell === cell.id)) && <CodeMirror value={drafts[cell.id] ?? source(cell)} basicSetup={{ lineNumbers: false, highlightActiveLineGutter: false, foldGutter: false }} theme={theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'} onFocus={() => { setSelectedCell(cell.id); setMode('edit') }} onBlur={() => setMode('command')} extensions={[
        ...(cell.cell_type === 'code' ? [python()] : []),
        Prec.highest(keymap.of([{ key: 'Shift-Enter', run: () => { if (runnable(cell.id)) { void nb.execute(cell.id); setSelectedCell(doc.cells[index + 1]?.id ?? cell.id) } return true } },
          { key: 'Mod-Enter', run: () => { if (runnable(cell.id)) void nb.execute(cell.id); return true } },
          { key: 'Mod-s', run: () => { void nb.save(); return true } }, { key: 'Escape', run: view => { view.contentDOM.blur(); setMode('command'); return true } }])),
      ]} editable={!busy} onChange={value => nb.edit(cell.id, value)} /> }
      {cell.cell_type === 'markdown' && (mode !== 'edit' || selectedCell !== cell.id) && <div className="markdown-preview" onDoubleClick={() => setMode('edit')}><Markdown skipHtml components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>[Image: {alt ?? ''}]</span> }}>{drafts[cell.id] ?? source(cell)}</Markdown></div>}
      {cell.cell_type === 'code' && !!cell.outputs?.length && <div className="cell-output"><div className="cell-gutter">Out [{cell.execution_count ?? ' '}]:</div><Outputs outputs={cell.outputs} /></div>}
      {!cell.metadata.europa && typeof cell.metadata.europa_recovered_output === 'string' && <p className="muted">Historical output copied from conflict receipt {cell.metadata.europa_recovered_output}. Not a fresh execution result.</p>}
      </div></div>
    </section>)}
    {receipts.length > 0 && <details className="receipt-history"><summary>Execution history ({receipts.length})</summary>{receipts.map(r => <div key={r.execution_id}><code>{r.execution_id.slice(0, 10)}</code> · {r.state} {r.result?.status}
      {r.state === 'conflict' && <button onClick={() => void nb.action(async () => setConflictResult(await request<Receipt>(`/notebooks/${id}/executions/${r.execution_id}`)))}>Inspect conflict outputs</button>}</div>)}</details>}
    {conflictResult && <section className="conflict"><h2>Conflict output — not applied</h2><p>Receipt {conflictResult.execution_id}. Newer edits are unchanged. You may explicitly copy historical output only if the source still matches. The receipt will remain conflict, never execution evidence of success. Re-run reviewed code for a fresh result.</p><Outputs outputs={conflictResult.result?.outputs ?? []} />
      <button disabled={busy || dirty || nb.active} onClick={() => { if (confirm('Replace this cell’s saved outputs with historical conflict output? Source must match and the current revision must still be valid.')) void nb.importConflict(conflictResult) }}>Copy historical output to unchanged cell</button>{' '}
      <button onClick={() => setConflictResult(null)}>Dismiss preview (receipt retained)</button></section>}
    <StatusSlot><footer className="status" role="status"><details><summary><span>API: {nb.connection}</span><span>Kernel: {nb.session.state} ({nb.session.kernels})</span><span>{busy ? 'Saving / requesting…' : nb.paused ? 'Autosave paused' : dirty ? 'Unsaved draft (browser recovery)' : doc ? 'Document saved' : 'No notebook selected'}</span><span>{nb.active ? 'Pending execution receipt' : 'No pending receipt'}{receipts.some(r => r.state === 'uncertain') ? ' · Uncertain receipt — review history' : ''}</span></summary><div className="status-details"><small>Saved outputs do not checkpoint Python variables. Execution history and receipts remain available in the notebook.</small></div></details></footer></StatusSlot></div></>}
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
