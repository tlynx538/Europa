import { useCallback, useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { ActivityPage, NotebookLibrary, ProjectDetail, ProjectsPage } from './Pages'
import { ApiError, request } from './api'
import './navigation.css'

export type Navigate = (path: string) => Promise<boolean>
export type Route = { kind: 'notebooks' | 'notebook' | 'projects' | 'project' | 'activity' | 'missing'; id?: string }

export function route(path: string): Route {
  if (path === '/notebooks') return { kind: 'notebooks' }
  if (path === '/projects') return { kind: 'projects' }
  if (path === '/activity') return { kind: 'activity' }
  const match = /^\/(notebooks|projects)\/([a-zA-Z0-9_-]+)$/.exec(path)
  if (match) return { kind: match[1] === 'notebooks' ? 'notebook' : 'project', id: match[2] }
  return { kind: 'missing' }
}

type Props = {
  openNotebook: (id: string) => Promise<boolean>
  renderNotebook: (id: string, navigate: Navigate) => ReactNode
  currentNotebookId?: string
  onNotebookDeleted?: (id: string) => void
  beforeLeave?: () => boolean
}

export function Navigation({ openNotebook, renderNotebook, currentNotebookId, onNotebookDeleted, beforeLeave }: Props) {
  const [path, setPath] = useState(() => {
    if (location.pathname === '/') history.replaceState(null, '', '/notebooks')
    return location.pathname
  })
  const current = useRef(path); current.current = path
  const [error, setError] = useState('')
  const [notebookState, setNotebookState] = useState<'ready' | 'loading' | 'missing' | 'unavailable'>(() => route(path).kind === 'notebook' ? 'loading' : 'ready')
  const navigating = useRef(false)
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
      if (route(current.current).kind === 'notebook' && next.kind !== 'notebook' && beforeLeave && !beforeLeave()) return false
      if (next.kind === 'notebook' && !await openNotebook(next.id!)) { await failedOpen(next.id!); return false }
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
      if (ok) setNotebookState('ready')
      else { const state = await failedOpen(target.id!); if (alive) setNotebookState(state) }
    }).catch(e => { if (alive) { setNotebookState('unavailable'); setError(String(e)) } })
    return () => { alive = false }
  }, []) // Direct deep link only; deliberate transitions are handled by navigate/popstate.
  useEffect(() => {
    const onPop = () => {
      const target = location.pathname, previous = current.current
      if (target === previous) return
      const next = route(target)
      if (route(previous).kind === 'notebook' && next.kind !== 'notebook' && beforeLeave && !beforeLeave()) {
        history.pushState(null, '', previous); return
      }
      if (next.kind !== 'notebook') { current.current = target; setPath(target); setError(''); setNotebookState('ready'); return }
      if (navigating.current) { history.pushState(null, '', previous); return }
      navigating.current = true
      void openNotebook(next.id!).then(ok => {
        if (ok) { current.current = target; setPath(target); setNotebookState('ready'); setError('') }
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
  return <div className="navigation"><nav className="navigation-bar" aria-label="Main navigation"><strong>Europa</strong>{link('/projects', 'Projects')}{link('/notebooks', 'Notebooks')}{link('/activity', 'Activity')}</nav>
    {error && <p role="alert" className="alert">{error}</p>}
    {selected.kind === 'projects' && <ProjectsPage navigate={navigate} />}
    {selected.kind === 'project' && <ProjectDetail id={selected.id!} navigate={navigate} />}
    {selected.kind === 'notebooks' && <NotebookLibrary navigate={navigate} currentNotebookId={currentNotebookId} onNotebookDeleted={onNotebookDeleted} />}
    {selected.kind === 'notebook' && (notebookState === 'missing' || notebookState === 'unavailable' ? <main className="browser-page"><h1>{notebookState === 'missing' ? 'Notebook not found' : 'Unable to open notebook'}</h1>{link('/notebooks', 'Back to notebooks')}</main> : notebookState === 'loading' ? <main className="browser-page"><p>Opening notebook…</p></main> : renderNotebook(selected.id!, navigate))}
    {selected.kind === 'activity' && <ActivityPage navigate={navigate} />}
    {selected.kind === 'missing' && <main className="browser-page"><h1>Page not found</h1>{link('/notebooks', 'Back to notebooks')}</main>}
  </div>
}
