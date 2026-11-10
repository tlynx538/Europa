import { useCallback, useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { ApiError, json, request } from './api'
import { all, change, pendingMutation, retryMutation, type Project, type RecordData } from './applicationApi'
import type { Meta } from './useNotebook'
import type { Navigate } from './Navigation'

type Recent = { notebook_id: string; opened_at: number }
export const recentVisitRecorded = 'europa:recent-visit-recorded'
const notebookPath = (id: string) => `/notebooks/${encodeURIComponent(id)}`
const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`
const icon = (kind: 'folder' | 'file') => <span className={`browser-icon browser-icon-${kind}`} aria-hidden="true" />

export function Home({ navigate }: { navigate: Navigate }) {
  const [notebooks, setNotebooks] = useState<Meta[]>([]), [projects, setProjects] = useState<RecordData<Project>[]>([])
  const [recent, setRecent] = useState<Recent[]>([]), [recentLoaded, setRecentLoaded] = useState(false)
  const [loaded, setLoaded] = useState(false), [error, setError] = useState(''), [recentError, setRecentError] = useState('')
  const [busy, setBusy] = useState(false), [pending, setPending] = useState(!!pendingMutation())
  const [query, setQuery] = useState(''), [sort, setSort] = useState('title-asc')
  const recentRequest = useRef(0)
  const refresh = useCallback(async () => {
    const [n, p] = await Promise.all([request<Meta[]>('/notebooks'), all<Project>('/projects')])
    setNotebooks(n); setProjects(p); setLoaded(true); setError('')
  }, [])
  const refreshRecent = useCallback(async () => {
    const requestNumber = ++recentRequest.current
    try { const data = await request<{ items: Recent[] }>('/notebooks/recent'); if (requestNumber === recentRequest.current) { setRecent(data.items); setRecentLoaded(true); setRecentError('') } }
    catch (e) { if (requestNumber === recentRequest.current) setRecentError(`Recent notebooks unavailable: ${String(e)}`) }
  }, [])
  useEffect(() => {
    const onVisit = () => { void refreshRecent() }
    addEventListener(recentVisitRecorded, onVisit)
    void refresh().catch(e => setError(String(e))); void refreshRecent()
    return () => { removeEventListener(recentVisitRecorded, onVisit); recentRequest.current++ }
  }, [refresh, refreshRecent])
  const run = async (operation: () => Promise<void>) => {
    if (busy) return
    setBusy(true); setError('')
    try { await operation() } catch (e) { setError(String(e)) }
    finally { setBusy(false); setPending(!!pendingMutation()) }
  }
  const link = (path: string, title: ReactNode) => <a href={path} onClick={(e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault(); void navigate(path)
  }}>{title}</a>
  const member = (id: string) => projects.find(p => p.data.notebooks.some(n => n.notebook_id === id))
  const matching = (title: string) => title.toLowerCase().includes(query.trim().toLowerCase())
  const ordered = (items: Meta[]) => [...items].sort((a, b) => sort === 'title-desc' ? b.title.localeCompare(a.title) : sort === 'revision-desc' ? b.revision - a.revision || a.title.localeCompare(b.title) : a.title.localeCompare(b.title))
  const standalone = ordered(notebooks.filter(n => !member(n.notebook_id) && matching(n.title)))
  const notebookRow = (n: Meta) => <li key={n.notebook_id}>{link(notebookPath(n.notebook_id), <>{icon('file')}{n.title}</>)}<span>{member(n.notebook_id)?.data.title ?? 'Standalone'} · Revision {n.revision}</span><span className="runtime-unknown" title="Runtime status unknown"><span className="runtime-dot" aria-hidden="true" /> Runtime: Unknown</span></li>
  return <main className="browser-page home-page"><div className="browser-heading"><h1>Welcome to Europa</h1><button disabled={busy} onClick={() => { void refresh().catch(e => setError(String(e))); void refreshRecent() }}>Refresh</button></div>
    <h2>Start</h2><p className="muted">Open a notebook or browse real files. Legacy projects and notebooks remain available separately.</p>
    <div className="home-actions"><button disabled={busy || pending} onClick={() => void run(async () => {
      const title = prompt('Project title')?.trim(); if (!title) return
      const created = await change<RecordData<Project>>('/projects', 'POST', { title })
      await navigate(projectPath(created.id))
    })}>New legacy project</button><button disabled={busy || pending} onClick={() => void run(async () => {
      const title = prompt('Notebook title')?.trim(); if (!title) return
      let created: { metadata: { europa: Meta } }
      try { created = await request('/notebooks', json('POST', { title })) }
      catch (e) {
        if (!(e instanceof ApiError) || !e.recoveryNotebookId) throw e
        await navigate(notebookPath(e.recoveryNotebookId))
        throw new Error(`Notebook ${e.recoveryNotebookId} was saved but its conversation is unavailable. Open the saved notebook and retry the canonical conversation there; do not create it again.`)
      }
      if (!await navigate(notebookPath(created.metadata.europa.notebook_id))) {
        await refresh()
        throw new Error(`Notebook ${created.metadata.europa.notebook_id} was created but could not be opened. Find it in standalone notebooks or retry opening it; do not create it again.`)
      }
    })}>New notebook</button></div>
    {error && <p role="alert" className="alert">{error}</p>}{recentError && <p role="alert" className="alert">{recentError}</p>}
    {pending && <p role="alert" className="alert">An application change has an unknown outcome. Retry the exact operation before changing anything else. <button disabled={busy} onClick={() => void run(async () => { await retryMutation(); location.reload() })}>Retry pending change</button></p>}
    <section aria-label="Recent notebooks"><h2>Recent notebooks</h2><div className="recent-labels"><span>Notebook</span><span>Location</span><span>Last opened</span><span>Runtime</span></div><ul className="browser-list recent-list">{recent.map(item => {
      const notebook = notebooks.find(n => n.notebook_id === item.notebook_id)
      return <li key={item.notebook_id}>{notebook ? link(notebookPath(item.notebook_id), <>{icon('file')}{notebook.title}</>) : <span title={`Notebook ${item.notebook_id} is unavailable`}>{icon('file')}Missing notebook</span>}<span aria-label={`Location: ${notebook ? member(item.notebook_id)?.data.title ?? 'Standalone' : 'Unavailable'}`}>{notebook ? member(item.notebook_id)?.data.title ?? 'Standalone' : 'Unavailable'}</span><time aria-label={`Last opened: ${new Date(item.opened_at * 1000).toLocaleString()}`} dateTime={new Date(item.opened_at * 1000).toISOString()}>{new Date(item.opened_at * 1000).toLocaleString()}</time><span className="runtime-unknown" title="Runtime status unknown"><span className="runtime-dot" aria-hidden="true" /> Runtime: Unknown</span></li>
    })}</ul>{recentLoaded && !recent.length && <p className="browser-empty">No recent notebooks yet.</p>}</section>
    <section aria-label="Browse"><h2>Browse</h2>{link('/browse', 'Browse files')}</section>
    <div className="browser-filters"><label>Search files <input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label><label>Sort notebooks <select value={sort} onChange={e => setSort(e.target.value)}><option value="title-asc">Title A–Z</option><option value="title-desc">Title Z–A</option><option value="revision-desc">Revision (highest first)</option></select></label></div>
    <section aria-label="Legacy projects"><h2>Legacy projects</h2><ul className="browser-list">{projects.filter(p => matching(p.data.title) || p.data.notebooks.some(n => notebooks.some(nb => nb.notebook_id === n.notebook_id && matching(nb.title)))).map(p => <li key={p.id}>{link(projectPath(p.id), <>{icon('folder')}{p.data.title}</>)}<span>Private catalog · {p.data.status} · {p.data.notebooks.filter(n => n.present).length} notebooks</span></li>)}</ul>{loaded && !projects.length && <p className="browser-empty">No legacy projects yet.</p>}</section>
    <section aria-label="Standalone notebooks"><h2>Standalone notebooks</h2><ul className="browser-list">{standalone.map(notebookRow)}</ul>{loaded && !standalone.length && <p className="browser-empty">{query ? 'No standalone notebooks match your search.' : 'No standalone notebooks. Create a notebook to get started.'}</p>}</section>
  </main>
}
