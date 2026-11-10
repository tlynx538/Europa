import { useCallback, useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { ActivityPage, NotebookLibrary, ProjectDetail, ProjectsPage } from './Pages'
import { ApiError, request } from './api'
import { recentVisitRecorded } from './Home'
import { PhysicalWorkspace, workspaceUrl } from './PhysicalWorkspace'
import { TextFileEditor } from './TextFileEditor'
import { pendingWorkspaceSave } from './TextFileEditor'
import './navigation.css'

export type Navigate = (path: string) => Promise<boolean>
export type Route = { kind: 'home' | 'browse' | 'text' | 'notebooks' | 'notebook' | 'projects' | 'project' | 'activity' | 'missing'; id?: string }

export function route(path: string): Route {
  if (path === '/browse' || path.startsWith('/browse?')) return { kind: 'browse' }
  if (path.startsWith('/text?')) return { kind: 'text' }
  if (path === '/' || path.startsWith('/?')) return { kind: 'home' }
  if (path === '/notebooks') return { kind: 'notebooks' }
  if (path === '/projects') return { kind: 'projects' }
  if (path === '/activity') return { kind: 'activity' }
  const match = /^\/(notebooks|projects)\/([a-zA-Z0-9_-]+)$/.exec(path)
  if (match) return { kind: match[1] === 'notebooks' ? 'notebook' : 'project', id: match[2] }
  return { kind: 'missing' }
}

type Props = {
  openNotebook: (id: string) => Promise<boolean>
  renderNotebook: (id: string, navigate: Navigate, closeTarget?: string) => ReactNode
  currentNotebookId?: string
  onNotebookDeleted?: (id: string) => void
  beforeLeave?: () => boolean
}

export function Navigation({ openNotebook, renderNotebook, currentNotebookId, onNotebookDeleted, beforeLeave }: Props) {
  const [path, setPath] = useState(() => {
    return location.pathname + location.search
  })
  const current = useRef(path); current.current = path
  const [error, setError] = useState('')
  const [recentWarning, setRecentWarning] = useState('')
  const lastRecorded = useRef<string | null>(null)
  const recordVisit = (id: string) => {
    if (lastRecorded.current === id) return
    lastRecorded.current = id
    setRecentWarning('')
    void request(`/notebooks/${encodeURIComponent(id)}/opened`, { method: 'POST' }).then(() => {
      dispatchEvent(new Event(recentVisitRecorded))
    }).catch(() => {
      setRecentWarning('Notebook opened, but recent history was not recorded.')
    })
  }
  const [notebookState, setNotebookState] = useState<'ready' | 'loading' | 'missing' | 'unavailable'>(() => route(path).kind === 'notebook' ? 'loading' : 'ready')
  const physicalOrigin = async (id: string) => {
    try {
      const location = await request<{ path?: string } | null>(`/workspace/notebooks/${encodeURIComponent(id)}/location`)
      return location?.path ? workspaceUrl(location.path.split('/').slice(0, -1).join('/')) : undefined
    } catch { return undefined }
  }
  const navigating = useRef(false)
  const [notebookOrigin, setNotebookOrigin] = useState<string | undefined>()
  const canLeave = (previous: string, target: string) => {
    if (route(previous).kind === 'notebook' && route(target).kind !== 'notebook' && beforeLeave && !beforeLeave()) return false
    if (route(previous).kind === 'text' && previous !== target) {
      const file = new URLSearchParams(previous.split('?')[1] ?? '').get('path') ?? ''
      if (pendingWorkspaceSave()) { setError('A text save has an unknown outcome. Retry the exact save before leaving.'); return false }
      if (localStorage.getItem(`europa.workspace.draft.${file}`) !== null && !confirm('Keep the browser text draft and leave?')) return false
    }
    return true
  }
  const failedOpen = async (id: string): Promise<'missing' | 'unavailable'> => {
    try { await request(`/notebooks/${encodeURIComponent(id)}`); setError('Notebook switch cancelled or failed; drafts retained.'); return 'unavailable' }
    catch (e) {
      if (e instanceof ApiError && e.status === 404) { setError('Notebook not found; previous notebook retained.'); return 'missing' }
      setError(String(e)); return 'unavailable'
    }
  }
  const navigate = useCallback<Navigate>(async target => {
    if (navigating.current) return false
    if (target === current.current) return true
    navigating.current = true
    try {
      const next = route(target)
      if (!canLeave(current.current, target)) return false
      let verifiedOrigin = next.kind === 'notebook' ? await physicalOrigin(next.id!) : undefined
      if (!verifiedOrigin && next.kind === 'notebook' && route(current.current).kind === 'project') {
        try {
          const projectId = route(current.current).id!
          const project = await request<{ data: { notebooks: { notebook_id: string; present: boolean }[] } }>(`/projects/${encodeURIComponent(projectId)}`)
          if (project.data.notebooks.some(member => member.notebook_id === next.id && member.present)) verifiedOrigin = current.current
        } catch { /* Unknown origin must never masquerade as a verified project. */ }
      }
      if (next.kind === 'notebook' && !await openNotebook(next.id!)) { await failedOpen(next.id!); return false }
      if (next.kind === 'notebook') setNotebookOrigin(verifiedOrigin ?? (route(current.current).kind === 'home' ? '/' : undefined))
      if (next.kind === 'notebook') recordVisit(next.id!)
      else { lastRecorded.current = null; setRecentWarning('') }
      history.pushState(null, '', target)
      current.current = target; setPath(target); setError(''); setNotebookState('ready')
      return true
    } catch (e) { setError(String(e)); return false }
    finally { navigating.current = false }
  }, [openNotebook, beforeLeave])
  useEffect(() => {
    let alive = true
    const target = route(path)
    if (target.kind === 'notebook') void openNotebook(target.id!).then(async ok => {
      if (!alive) return
      if (ok) { const origin = await physicalOrigin(target.id!); if (!alive) return; setNotebookOrigin(origin); setNotebookState('ready'); recordVisit(target.id!) }
      else { const state = await failedOpen(target.id!); if (alive) setNotebookState(state) }
    }).catch(e => { if (alive) { setNotebookState('unavailable'); setError(String(e)) } })
    return () => { alive = false }
  }, []) // Direct deep link only; deliberate transitions are handled by navigate/popstate.
  useEffect(() => {
    const onPop = () => {
      const target = location.pathname + location.search, previous = current.current
      if (target === previous) return
      const next = route(target)
      if (navigating.current) { history.pushState(null, '', previous); return }
      if (!canLeave(previous, target)) {
        history.pushState(null, '', previous); return
      }
      if (next.kind !== 'notebook') { lastRecorded.current = null; setRecentWarning(''); current.current = target; setPath(target); setError(''); setNotebookState('ready'); return }
      navigating.current = true
      void openNotebook(next.id!).then(async ok => {
        if (ok) { setNotebookOrigin(await physicalOrigin(next.id!)); recordVisit(next.id!); current.current = target; setPath(target); setNotebookState('ready'); setError('') }
        else { history.pushState(null, '', previous); void failedOpen(next.id!) }
      }).catch(e => { history.pushState(null, '', previous); setError(String(e)) }).finally(() => { navigating.current = false })
    }
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [openNotebook, beforeLeave])
  const link = (target: string, label: string) => <a href={target} aria-current={path === target ? 'page' : undefined} onClick={(e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault(); void navigate(target)
  }}>{label}</a>
  const selected = route(path)
  return <div className="navigation">{selected.kind !== 'home' && selected.kind !== 'browse' && <nav className="navigation-bar" aria-label="Main navigation">{link('/', 'Europa')}</nav>}
    {error && <p role="alert" className="alert">{error}</p>}
    {recentWarning && <p role="alert" className="alert">{recentWarning}</p>}
    {(selected.kind === 'home' || selected.kind === 'browse') && <PhysicalWorkspace key={new URLSearchParams(path.split('?')[1] ?? '').get('path') ?? ''} path={new URLSearchParams(path.split('?')[1] ?? '').get('path') ?? ''} tab={new URLSearchParams(path.split('?')[1] ?? '').get('view') === 'running' ? 'running' : 'files'} navigate={navigate} />}
    {selected.kind === 'text' && <TextFileEditor path={new URLSearchParams(path.split('?')[1] ?? '').get('path') ?? ''} navigate={navigate} />}
    {selected.kind === 'projects' && <ProjectsPage navigate={navigate} />}
    {selected.kind === 'project' && <ProjectDetail id={selected.id!} navigate={navigate} />}
    {selected.kind === 'notebooks' && <NotebookLibrary navigate={navigate} currentNotebookId={currentNotebookId} onNotebookDeleted={onNotebookDeleted} />}
     {selected.kind === 'notebook' && (notebookState === 'missing' || notebookState === 'unavailable' ? <main className="browser-page"><h1>{notebookState === 'missing' ? 'Notebook not found' : 'Unable to open notebook'}</h1>{link('/notebooks', 'Back to notebooks')}</main> : notebookState === 'loading' ? <main className="browser-page"><p>Opening notebook…</p></main> : renderNotebook(selected.id!, navigate, notebookOrigin))}
    {selected.kind === 'activity' && <ActivityPage navigate={navigate} />}
    {selected.kind === 'missing' && <main className="browser-page"><h1>Page not found</h1>{link('/notebooks', 'Back to notebooks')}</main>}
  </div>
}
