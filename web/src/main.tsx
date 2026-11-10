import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { keymap } from '@codemirror/view'
import { Prec } from '@codemirror/state'
import Markdown from 'react-markdown'
import { python } from '@codemirror/lang-python'
import { request, type Receipt } from './api'
import { Outputs } from './Output'
import { EditorIcon } from './EditorIcon'
import * as Menubar from '@radix-ui/react-menubar'
import { Workspace } from './Workspace'
import { ActionMenu, ActionItem, ActionSeparator } from './ActionMenu'
import { groupReceipts } from './receiptGroups'
import { CommentIndicator, HeaderSlot, Layout, StatusSlot, usePanels } from './Layout'
import { source, useNotebook } from './useNotebook'
import { Navigation, type Navigate } from './Navigation'
import { all, type Project, type RecordData } from './applicationApi'
import './style.css'

function MenuItem({ children, onSelect, disabled, title, shortcut }: { children: React.ReactNode; onSelect: () => void; disabled?: boolean; title?: string; shortcut?: string }) {
  return <Menubar.Item className="menu-row" disabled={disabled} title={title} onSelect={onSelect}>{children}{shortcut && <span className="menu-shortcut" aria-hidden="true">{shortcut}</span>}</Menubar.Item>
}
function Menu({ name, children }: { name: string; children: React.ReactNode }) {
  return <Menubar.Menu value={name}><Menubar.Trigger className="menu-trigger">{name}</Menubar.Trigger><Menubar.Portal><Menubar.Content aria-label={name} className="menu-content" align="start" sideOffset={4} collisionPadding={8}>{children}</Menubar.Content></Menubar.Portal></Menubar.Menu>
}
function PanelMenuItem({ side }: { side: 'left' | 'right' }) {
  const panels = usePanels()
  return side === 'left' ? <Menubar.CheckboxItem className="menu-row" checked={panels.left} onCheckedChange={panels.setLeft}><Menubar.ItemIndicator className="menu-indicator">✓</Menubar.ItemIndicator>Roadmap panel</Menubar.CheckboxItem> : <MenuItem onSelect={() => document.querySelector<HTMLTextAreaElement>('.message-composer textarea')?.focus()}>Focus discussion</MenuItem>
}
function ProjectSettingsMenuItem() {
  const panels = usePanels()
  return <MenuItem onSelect={panels.openSettings}>Project settings…</MenuItem>
}
function App() {
  const nb = useNotebook()
  const { doc, drafts, busy, error, remote, dirty, receipts } = nb
  const id = doc?.metadata.europa.notebook_id
  const [parent, setParent] = useState<RecordData<Project> | null>(null)
  const [parentReady, setParentReady] = useState(false)
  const [parentForId, setParentForId] = useState<string | null>(null)
  useEffect(() => {
    if (!id) { setParent(null); setParentReady(false); setParentForId(null); return }
    let live = true
    setParent(null); setParentReady(false); setParentForId(null)
    void all<Project>('/projects').then(projects => {
      if (live) { setParent(projects.find(project => project.data.notebooks.some(member => member.notebook_id === id && member.present)) ?? null); setParentForId(id); setParentReady(true) }
    }).catch(() => { if (live) { setParent(null); setParentReady(false); setParentForId(null) } })
    return () => { live = false }
  }, [id])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [selectedCell, setSelectedCell] = useState<string | undefined>()
  const [cellMenu, setCellMenu] = useState<string | null>(null)
  const discussingCell = useRef(false)
  const pendingCell = useRef<{ notebook: string; cell: string } | null>(null)
  const pendingAppend = useRef<{ notebook: string; cells: Set<string> } | null>(null)
  const notebookScroll = useRef<HTMLDivElement>(null)
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
  const canRun = !busy && !nb.active && !nb.paused && !remote
  const runnable = (cell?: string) => canRun && !!doc?.cells.some(c => c.id === cell && c.cell_type === 'code')
  const copy = () => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell) setClipboard({ cell_type: cell.cell_type, source: drafts[cell.id] ?? source(cell) }) }
  const cut = () => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && !dirty && confirm('Cut selected cell?')) { setClipboard({ cell_type: cell.cell_type, source: source(cell) }); void nb.remove(cell.id) } }
  const paste = () => { if (clipboard) void nb.paste(clipboard, selectedCell ? (doc?.cells.findIndex(c => c.id === selectedCell) ?? -1) + 1 : doc?.cells.length ?? 0) }
  const insert = () => {
    if (!doc || busy || nb.active || remote) return
    pendingAppend.current = { notebook: doc.metadata.europa.notebook_id, cells: new Set(doc.cells.map(cell => cell.id)) }
    void nb.mutate('/cells', { cell_type: 'code', index: doc.cells.length })
  }
  const revealCell = (cell: string) => {
    const element = document.getElementById(`cell-${cell}`)
    element?.scrollIntoView({ behavior: 'instant', block: 'nearest' })
    const top = (document.querySelector('.appbar')?.getBoundingClientRect().bottom ?? 0) + 8
    if (element && element.getBoundingClientRect().top < top) element.closest<HTMLElement>('.notebook-editor')?.scrollBy({ top: element.getBoundingClientRect().top - top, behavior: 'instant' })
  }
  const selectAndReveal = (cell: string) => { setSelectedCell(cell); revealCell(cell) }
  useEffect(() => {
    const pending = pendingAppend.current
    if (!pending) return
    if (id !== pending.notebook) { pendingAppend.current = null; return }
    const appended = doc?.cells.find(cell => !pending.cells.has(cell.id))
    if (appended) {
      pendingAppend.current = null
      setSelectedCell(appended.id)
      requestAnimationFrame(() => {
        if (location.pathname !== `/notebooks/${pending.notebook}`) return
        const element = document.getElementById(`cell-${appended.id}`)
        const composer = document.querySelector('.composer-shell')?.getBoundingClientRect()
        if (!element || !composer) return
        const scroll = notebookScroll.current
        if (!scroll) return
        const bottom = element.getBoundingClientRect().bottom
        if (bottom > composer.top - 8) scroll.scrollBy({ top: bottom - composer.top + 8, behavior: 'instant' })
        revealCell(appended.id)
      })
    } else if (!busy && error) pendingAppend.current = null
  }, [id, doc, busy, error])
  useEffect(() => {
    const command = (event: KeyboardEvent) => {
      if (!location.pathname.startsWith('/notebooks/') || event.altKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || (event.target instanceof HTMLElement && (event.target.isContentEditable || !!event.target.closest('.cm-editor, button, summary, dialog, [role=menubar], [role=menu]')))) return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void nb.save(); return }
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && selectedCell && doc?.cells.find(c => c.id === selectedCell)?.cell_type === 'code') { event.preventDefault(); if (runnable(selectedCell)) void nb.execute(selectedCell); return }
      if (event.metaKey || event.ctrlKey) return
      if (mode !== 'command' || !selectedCell || !doc || busy || nb.active) return
      const index = doc.cells.findIndex(c => c.id === selectedCell)
      if (event.key === 'j' || event.key === 'ArrowDown') selectAndReveal(doc.cells[index + 1]?.id ?? selectedCell)
      else if (event.key === 'k' || event.key === 'ArrowUp') selectAndReveal(doc.cells[index - 1]?.id ?? selectedCell)
      else if (event.key === 'c') copy()
      else if (event.key === 'v') paste()
      else if (event.key === 'x' && !dirty && !remote) cut()
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
  useEffect(() => { pendingAppend.current = null; setConflictResult(null); const pending = pendingCell.current; setSelectedCell(pending && pending.notebook === id && doc?.cells.some(c => c.id === pending.cell) ? pending.cell : undefined); pendingCell.current = null; setMode('command') }, [id])
  useEffect(() => {
    if (!id || !doc || notebookScroll.current === null) return
    const saved = Number(sessionStorage.getItem(`europa.notebook.scroll.${id}`))
    if (Number.isFinite(saved) && saved >= 0) notebookScroll.current.scrollTop = saved
  }, [id])
  useEffect(() => {
    if (mode !== 'edit' || !selectedCell || doc?.cells.find(cell => cell.id === selectedCell)?.cell_type !== 'markdown') return
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>(`#cell-${CSS.escape(selectedCell)} .cm-content`)?.focus())
    return () => cancelAnimationFrame(frame)
  }, [mode, selectedCell, id])
  const latest = Object.fromEntries(receipts.map(r => [r.cell_id, r]))
  const history = groupReceipts(receipts, doc?.cells.map(c => c.id) ?? [])
  const revealHistory = () => {
    const cell = selectedCell && history.cells[selectedCell]?.length ? selectedCell : doc?.cells.find(c => history.cells[c.id]?.length)?.id
    const target = cell ? document.querySelector<HTMLDetailsElement>(`[data-history-cell="${CSS.escape(cell)}"]`) : document.querySelector<HTMLDetailsElement>('.orphan-receipts')
    if (!target) return
    target.open = true
    requestAnimationFrame(() => target.scrollIntoView({ block: 'nearest' }))
  }
  const receiptRows = (items: Receipt[]) => items.map(r => <div className="receipt-row" key={r.execution_id}><code>{r.execution_id}</code> · {r.state}{r.result ? ` · ${r.result.status}` : ''}
    {r.state === 'conflict' && <button onClick={() => void nb.action(async () => setConflictResult(await request<Receipt>(`/notebooks/${id}/executions/${r.execution_id}`)))}>Inspect conflict outputs</button>}</div>)
  const openNotebook = useCallback(async (target: string) => target === nb.doc?.metadata.europa.notebook_id ? true : !!await nb.open(target), [nb])
  const editor = (routeId: string, navigate: Navigate, verifiedOrigin?: string) => <Layout><HeaderSlot><div className="notebook-chrome-title"><a className="notebook-wordmark" href="/" onClick={e => { e.preventDefault(); void navigate('/') }}>Europa</a><nav className="notebook-breadcrumb" aria-label="Breadcrumb"><a href="/" onClick={e => { e.preventDefault(); void navigate('/') }}>Home</a>{parent && <> / <a href={`/projects/${encodeURIComponent(parent.id)}`} onClick={e => { e.preventDefault(); void navigate(`/projects/${encodeURIComponent(parent.id)}`) }}>{parent.data.title}</a></>} / <h1>{doc?.metadata.europa.title ?? 'Notebook'}</h1></nav><span className="save-indicator">{dirty ? 'Unsaved draft' : doc ? 'Saved' : ''} · Revision {doc?.metadata.europa.revision ?? '—'}</span></div><Menubar.Root className="notebook-menubar" value={openMenu ?? ''} onValueChange={value => setOpenMenu(value || null)}>
    <Menu name="File"><MenuItem onSelect={() => void navigate('/')}>Return Home</MenuItem><MenuItem onSelect={() => void navigate('/projects')}>Open project…</MenuItem><MenuItem onSelect={() => void navigate('/notebooks')}>Open notebook…</MenuItem><MenuItem onSelect={() => void navigate('/')}>New notebook…</MenuItem><MenuItem disabled={busy || !dirty || !!remote} onSelect={() => void nb.save()} shortcut="⌘/Ctrl S">Save</MenuItem>
    {doc && <><Menubar.Separator className="menu-separator" /><MenuItem disabled={busy || dirty} onSelect={() => { const title = prompt('Notebook title', doc.metadata.europa.title)?.trim(); if (title) void nb.mutate('', { title }, 'PATCH') }}>Rename</MenuItem><MenuItem disabled={busy} onSelect={() => { if (!dirty || confirm('Discard browser drafts and reload saved notebook?')) void nb.open(id!, true) }}>Reload saved notebook</MenuItem></>}<Menubar.Separator className="menu-separator" /><ProjectSettingsMenuItem /><Menubar.Separator className="menu-separator" /><MenuItem disabled={!verifiedOrigin && !(parentReady && parentForId === routeId)} title={!verifiedOrigin && !(parentReady && parentForId === routeId) ? 'Waiting for verified notebook parent' : undefined} onSelect={() => void navigate(verifiedOrigin ?? (parentForId === routeId && parent ? `/projects/${encodeURIComponent(parent.id)}` : '/'))}>Close Notebook</MenuItem></Menu>
    <Menu name="Edit"><MenuItem disabled={!selectedCell || busy || nb.active} onSelect={copy} shortcut="C">Copy cell</MenuItem><MenuItem disabled={!selectedCell || busy || nb.active || dirty || !!remote} title={dirty ? 'Save all drafts before cutting a cell. Copy and paste remain available.' : 'Cut selected cell'} onSelect={cut} shortcut="X">Cut cell</MenuItem><MenuItem disabled={!clipboard || busy || nb.active} onSelect={paste} shortcut="V">Paste cell</MenuItem><Menubar.Separator className="menu-separator" /><MenuItem disabled={!selectedCell || busy || nb.active} onSelect={() => { const cell = doc?.cells.find(c => c.id === selectedCell); if (cell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${cell.id}/type`, { cell_type: cell.cell_type === 'code' ? 'markdown' : 'code' }) }}>Toggle cell type</MenuItem><MenuItem disabled={!selectedCell || busy || nb.active || dirty} onSelect={() => { if (selectedCell && confirm('Delete selected cell?')) void nb.remove(selectedCell) }}>Delete cell</MenuItem></Menu>
    <Menu name="View"><Menubar.Sub><Menubar.SubTrigger className="menu-row">Theme <span className="menu-shortcut" aria-hidden="true">›</span></Menubar.SubTrigger><Menubar.Portal><Menubar.SubContent className="menu-content" collisionPadding={8} sideOffset={4}><Menubar.RadioGroup value={theme} onValueChange={setTheme}>{['system', 'light', 'dark'].map(value => <Menubar.RadioItem key={value} className="menu-row" value={value}><Menubar.ItemIndicator className="menu-indicator">●</Menubar.ItemIndicator>{value[0].toUpperCase() + value.slice(1)}</Menubar.RadioItem>)}</Menubar.RadioGroup></Menubar.SubContent></Menubar.Portal></Menubar.Sub><Menubar.Separator className="menu-separator" /><Menubar.CheckboxItem className="menu-row" checked={nb.autosave} onCheckedChange={nb.setAutosave}><Menubar.ItemIndicator className="menu-indicator">✓</Menubar.ItemIndicator>Autosave</Menubar.CheckboxItem><PanelMenuItem side="left" /><Menubar.Separator className="menu-separator" /><MenuItem disabled={!receipts.length} onSelect={revealHistory}>Execution history</MenuItem><MenuItem onSelect={() => setActivity(!activity)}>Contextual activity</MenuItem></Menu>
    <Menu name="Run"><MenuItem disabled={!runnable(selectedCell)} onSelect={() => { if (selectedCell && runnable(selectedCell)) void nb.execute(selectedCell) }}>Run selected cell</MenuItem><MenuItem disabled={!canRun} onSelect={() => void nb.runAll()}>Run all (sequential)</MenuItem></Menu>
    <Menu name="Kernel"><MenuItem onSelect={() => { setOpenMenu(null); setKernelOpen(true) }}>Kernel information</MenuItem></Menu>
    <Menu name="Help"><MenuItem onSelect={() => { setOpenMenu(null); setShortcutsOpen(true) }}>Keyboard shortcuts</MenuItem></Menu>
    </Menubar.Root>{id === routeId && doc && <div className="toolbar editor-tools" role="toolbar" aria-label="Notebook commands" onMouseDown={event => { if ((event.target as HTMLElement).closest('button')) event.preventDefault() }}><span className="editor-mode">Mode: {mode}</span>
      <button aria-label="Save notebook" title="Save notebook" disabled={busy || !dirty || !!remote} onClick={() => void nb.save()}><EditorIcon name="save" /></button>
      <button aria-label="Insert cell" title="Add code cell at end" disabled={busy || nb.active || !!remote} onClick={insert}><EditorIcon name="add" /></button>
      <span className="toolbar-divider" aria-hidden="true" />
      <button aria-label="Cut cell" title={dirty ? 'Save all drafts before cutting a cell' : 'Cut selected cell'} disabled={!selectedCell || busy || nb.active || dirty || !!remote} onClick={cut}><EditorIcon name="cut" /></button>
      <button aria-label="Copy cell" title="Copy selected cell" disabled={!selectedCell} onClick={copy}><EditorIcon name="copy" /></button>
      <button aria-label="Paste cell" title="Paste cell below" disabled={!clipboard || busy || nb.active || !!remote} onClick={paste}><EditorIcon name="paste" /></button>
      <span className="toolbar-divider" aria-hidden="true" />
      <button aria-label="Run selected cell" title="Run selected cell" disabled={!runnable(selectedCell)} onClick={() => { if (selectedCell && runnable(selectedCell)) void nb.execute(selectedCell) }}><EditorIcon name="run" /></button>
      <button aria-label="Interrupt selected execution" title="Interrupt selected execution" disabled={!selectedCell || latest[selectedCell]?.state !== 'running' || busy} onClick={() => { if (selectedCell && latest[selectedCell]?.state === 'running') void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[selectedCell].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') }) }}><EditorIcon name="interrupt" /></button>
      <label>Cell type <select aria-label="Cell type" value={doc.cells.find(c => c.id === selectedCell)?.cell_type ?? 'code'} disabled={!selectedCell || busy || nb.active} onChange={e => { if (selectedCell && confirm('Change cell type? Previous execution outputs will be cleared.')) void nb.mutate(`/cells/${selectedCell}/type`, { cell_type: e.target.value }) }}><option value="code">Code</option><option value="markdown">Markdown</option></select></label>
      <span className="toolbar-kernel" title={`Observed kernel: ${nb.session.state} (${nb.session.kernels})`}>Kernel: {nb.session.state} ({nb.session.kernels})</span>
    </div>}</HeaderSlot>{id === routeId && <Workspace notebookId={id} revision={doc?.metadata.europa.revision} cells={doc?.cells.map(c => ({ id: c.id, source: source(c) })) ?? []} draftCells={Object.keys(drafts)} conflict={!!remote} selectedCell={selectedCell} showActivity={activity} onCommentCounts={updateCounts} navigate={navigate} onOpenNotebook={async (notebook, cell) => {
    if (notebook !== id && cell) pendingCell.current = { notebook, cell }
    if (!await navigate(`/notebooks/${notebook}`)) { pendingCell.current = null; return false }
    if (notebook === id && cell && doc?.cells.some(c => c.id === cell)) selectAndReveal(cell)
    if (notebook !== id && cell) requestAnimationFrame(() => revealCell(cell))
    return true
  }} renderNotebook={canvas => <main className="notebook-column"><div className="notebook-editor" ref={notebookScroll} onScroll={event => { if (id === routeId) sessionStorage.setItem(`europa.notebook.scroll.${routeId}`, String(event.currentTarget.scrollTop)) }}>
    <dialog ref={shortcuts} className="editor-dialog" aria-label="Keyboard shortcuts" onClose={() => { setShortcutsOpen(false); document.querySelector<HTMLElement>('.notebook-menubar [data-state][aria-haspopup=menu]:last-of-type')?.focus() }}><h2>Keyboard shortcuts</h2><p>Shift+Enter runs a code cell and selects the next cell; Ctrl/Cmd+Enter runs without advancing; Ctrl/Cmd+S saves drafts. Escape leaves edit mode. In command mode: J/K or arrows select cells, Enter edits, A/B inserts above/below, C copies, V pastes, X cuts saved cells, D D deletes saved cells, M/Y changes cell type. Receipt history records execution outcomes. Kernel restart is unavailable.</p><button onClick={() => setShortcutsOpen(false)}>Close shortcuts</button></dialog>
    <dialog ref={kernelInfo} className="editor-dialog" aria-label="Kernel information" onClose={() => { setKernelOpen(false); Array.from(document.querySelectorAll<HTMLElement>('.notebook-menubar [aria-haspopup=menu]')).find(element => element.textContent === 'Kernel')?.focus() }}><h2>Kernel information</h2><p>Observed kernel: {nb.session.state} ({nb.session.kernels}). Restart and shutdown are unavailable without a verified notebook-scoped control contract. Saved outputs do not preserve interpreter state.</p><button onClick={() => setKernelOpen(false)}>Close kernel information</button></dialog>
    {id !== routeId && <p role="status">Opening notebook…</p>}
    {id === routeId && <>
    <div className="notebook-surface">
    {error && <p role="alert" className="alert">{error}</p>}
    {nb.paused && !remote && <button disabled={busy} onClick={nb.resume}>Resume autosave after review</button>}
    {doc && dirty && <button disabled={busy} onClick={() => void nb.compare()}>Compare with saved notebook</button>}
    {remote && <section className="conflict"><h2>Compare before resolving</h2><p>Server revision {remote.metadata.europa.revision}. Reapplying drafts requires explicit review and a new revision-checked save.</p>
      {Object.entries(drafts).map(([cell, value]) => <div key={cell}><h3>Cell {cell}</h3><strong>Saved source</strong><pre>{remote.cells.some(c => c.id === cell) ? source(remote.cells.find(c => c.id === cell)!) : '(deleted on server)'}</pre><strong>Your draft</strong><pre>{value}</pre></div>)}
      <button disabled={Object.keys(drafts).some(cell => !remote.cells.some(c => c.id === cell))} onClick={() => nb.resolve(true)}>Keep drafts for review</button>{' '}
      <button onClick={() => nb.resolve(false)}>Discard drafts and use saved version</button><p>Copy drafts of deleted cells before discarding them.</p>
    </section>}
    {canvas.beforeCells}
    {doc?.cells.map((cell, index) => <React.Fragment key={cell.id}><section className={`cell ${selectedCell === cell.id ? 'selected' : ''} ${selectedCell === cell.id && mode === 'edit' ? 'editing' : ''}`} id={`cell-${cell.id}`} tabIndex={0} aria-label={`${cell.cell_type} cell ${index + 1}`} onFocus={event => { if (!(event.target instanceof Node) || !event.currentTarget.contains(event.target)) return; setSelectedCell(cell.id); if (!(event.target as HTMLElement).closest('.cell-actions')) setCellMenu(null) }} onClick={event => { if (!(event.target instanceof Node) || !event.currentTarget.contains(event.target)) return; setSelectedCell(cell.id); if (!(event.target as HTMLElement).closest('.cell-actions')) setCellMenu(null) }}>
      <div className="cell-row"><div className="cell-gutter">{cell.cell_type === 'code' ? `In [${cell.execution_count ?? ' '}]:` : ''}</div><div className="cell-body">
      <div className="cell-meta">{counts[cell.id] ? <CommentIndicator cell={cell.id} count={counts[cell.id]} /> : null}
        <div className="cell-move" role="group" aria-label={`Reorder cell ${index + 1}`}>
          <button aria-label={`Move cell ${index + 1} up`} title={`Move cell ${index + 1} up`} disabled={busy || nb.active || !!remote || index === 0} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index - 1 })}><EditorIcon name="up" /></button>
          <button aria-label={`Move cell ${index + 1} down`} title={`Move cell ${index + 1} down`} disabled={busy || nb.active || !!remote || index === doc.cells.length - 1} onClick={() => void nb.mutate(`/cells/${cell.id}/move`, { index: index + 1 })}><EditorIcon name="down" /></button>
        </div>
        <ActionMenu label={`Cell actions for cell ${index + 1}`} trigger="Cell actions" triggerClassName="cell-actions" modal={false} open={cellMenu === cell.id} onOpenChange={open => { setCellMenu(open ? cell.id : null); if (open) setOpenMenu(null) }} onCloseAutoFocus={event => { if (discussingCell.current) { event.preventDefault(); discussingCell.current = false } }}>
          <ActionItem className="menu-row" onSelect={() => { discussingCell.current = true; canvas.discussCell(cell.id) }}>Discuss cell</ActionItem>
          <ActionSeparator className="menu-separator" />
          <ActionItem className="menu-row" disabled={busy || nb.active || !!remote} onSelect={() => void nb.mutate('/cells', { cell_type: 'code', index })}>Insert above</ActionItem>
          <ActionItem className="menu-row" disabled={busy || nb.active || !!remote} onSelect={() => void nb.mutate('/cells', { cell_type: 'code', index: index + 1 })}>Insert below</ActionItem>
          <ActionItem className="menu-row" disabled={busy || drafts[cell.id] === undefined || !!remote} onSelect={() => void nb.save()}>Save cell</ActionItem>
          {cell.cell_type === 'code' && <ActionItem className="menu-row" disabled={!runnable(cell.id)} onSelect={() => { if (runnable(cell.id)) void nb.execute(cell.id) }}>Run</ActionItem>}
          {latest[cell.id]?.state === 'running' && <ActionItem className="menu-row" disabled={busy} onSelect={() => void nb.action(async () => { await request(`/notebooks/${id}/executions/${latest[cell.id].execution_id}/interrupt`, { method: 'POST' }); nb.setError('Interrupt requested; waiting for the authoritative receipt result.') })}>Interrupt</ActionItem>}
          <ActionSeparator className="menu-separator" />
          <ActionItem className="menu-row destructive" disabled={busy || nb.active || !!remote || dirty} onSelect={() => { if (confirm('Delete this cell?')) void nb.remove(cell.id) }}>Delete</ActionItem>
        </ActionMenu>
      </div>
      {(cell.cell_type === 'code' || (mode === 'edit' && selectedCell === cell.id)) && <CodeMirror value={drafts[cell.id] ?? source(cell)} basicSetup={{ lineNumbers: false, highlightActiveLine: false, highlightActiveLineGutter: false, foldGutter: false }} theme={theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'} onFocus={() => { setSelectedCell(cell.id); setMode('edit') }} onBlur={() => setMode('command')} extensions={[
        ...(cell.cell_type === 'code' ? [python()] : []),
        Prec.highest(keymap.of([{ key: 'Shift-Enter', run: () => { if (runnable(cell.id)) { void nb.execute(cell.id); selectAndReveal(doc.cells[index + 1]?.id ?? cell.id) } return true } },
          { key: 'Mod-Enter', run: () => { if (runnable(cell.id)) void nb.execute(cell.id); return true } },
          { key: 'Mod-s', run: () => { void nb.save(); return true } }, { key: 'Escape', run: view => { view.contentDOM.blur(); setMode('command'); return true } }])),
      ]} editable={!busy} onChange={value => nb.edit(cell.id, value)} /> }
      {cell.cell_type === 'markdown' && (mode !== 'edit' || selectedCell !== cell.id) && <div className="markdown-preview" onDoubleClick={() => setMode('edit')}><Markdown skipHtml components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>[Image: {alt ?? ''}]</span> }}>{drafts[cell.id] ?? source(cell)}</Markdown></div>}
      {cell.cell_type === 'code' && !!cell.outputs?.length && <div className="cell-output"><Outputs outputs={cell.outputs} /></div>}
      {!cell.metadata.europa && typeof cell.metadata.europa_recovered_output === 'string' && <p className="muted">Historical output copied from conflict receipt {cell.metadata.europa_recovered_output}. Not a fresh execution result.</p>}
      {!!history.cells[cell.id]?.length && <div className="cell-history"><details className="receipt-history cell-receipts" data-history-cell={cell.id}><summary aria-label={`Execution history for cell ${index + 1}`} title={`Execution history for cell ${index + 1}`}><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span>{history.cells[cell.id].length}</span><span className="visually-hidden"> execution receipts for cell {index + 1}</span></summary>
        <div className="receipt-rows">{receiptRows(history.cells[cell.id])}</div></details>
        {latest[cell.id] && <p role="status" className={latest[cell.id].state === 'applied' && latest[cell.id].result?.status === 'ok' && cell.metadata.europa?.execution_id === latest[cell.id].execution_id && drafts[cell.id] === undefined ? 'routine-execution' : 'execution-notice'}>{latest[cell.id].state === 'applied' && cell.metadata.europa?.execution_id !== latest[cell.id].execution_id ? 'Previous execution' : 'Execution'}: {latest[cell.id].state}{latest[cell.id].result?.status ? ` (${latest[cell.id].result?.status})` : ''}{drafts[cell.id] !== undefined ? ' · draft not executed' : ''}</p>}</div>}
      </div></div>
    </section>{canvas.afterCell(cell.id)}</React.Fragment>)}
    {canvas.history}
    {history.orphans.length > 0 && <details className="receipt-history orphan-receipts"><summary>Execution history for deleted cells ({history.orphans.length})</summary><div className="receipt-rows">{history.orphans.map(r => <div key={r.execution_id}><strong>Deleted cell {r.cell_id}</strong>{receiptRows([r])}</div>)}</div></details>}
    {conflictResult && <section className="conflict"><h2>Conflict output — not applied</h2><p>Receipt {conflictResult.execution_id}. Newer edits are unchanged. You may explicitly copy historical output only if the source still matches. The receipt will remain conflict, never execution evidence of success. Re-run reviewed code for a fresh result.</p><Outputs outputs={conflictResult.result?.outputs ?? []} />
      <button disabled={busy || dirty || nb.active} onClick={() => { if (confirm('Replace this cell’s saved outputs with historical conflict output? Source must match and the current revision must still be valid.')) void nb.importConflict(conflictResult) }}>Copy historical output to unchanged cell</button>{' '}
      <button onClick={() => setConflictResult(null)}>Dismiss preview (receipt retained)</button></section>}
    </div><StatusSlot><footer className="status" role="status"><details><summary><span>API: {nb.connection}</span><span>Kernel: {nb.session.state} ({nb.session.kernels})</span><span>{busy ? 'Saving / requesting…' : nb.paused ? 'Autosave paused' : dirty ? 'Unsaved draft (browser recovery)' : doc ? 'Document saved' : 'No notebook selected'}</span><span>{nb.active ? 'Pending execution receipt' : 'No pending receipt'}{receipts.some(r => r.state === 'uncertain') ? ' · Uncertain receipt — review history' : ''}</span></summary><div className="status-details"><small>Saved outputs do not checkpoint Python variables. Execution history and receipts remain available in the notebook.</small></div></details></footer></StatusSlot></>}
  </div>{canvas.composer}</main>} />}</Layout>
  return <Navigation openNotebook={openNotebook} renderNotebook={editor} currentNotebookId={id} onNotebookDeleted={nb.forget} beforeLeave={() => {
    if (busy || nb.active) { nb.setError('Notebook operation or execution receipt is still pending. Wait for it to settle before leaving; execution is not cancelled.'); return false }
    if (dirty && !confirm('Keep drafts in this browser and leave the notebook?')) return false
    return nb.close()
  }} />
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
