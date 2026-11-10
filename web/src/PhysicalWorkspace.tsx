import { useEffect, useRef, useState } from 'react'
import { json, request } from './api'
import { Dashboard, type DashboardEntry } from './Dashboard'
import { useDashboardData } from './useDashboardData'
import type { Navigate } from './Navigation'

type Entry = { name: string; type: 'directory' | 'file'; modified: number; size: number | null; project_id?: string }
type Listing = { path: string; items: Entry[]; next_cursor: string | null }
type Operation = { kind: 'notebook' | 'open' | 'project' | 'folder' | 'text'; path: string; operation_id: string }
const pendingKey = 'europa.dashboard.operation'
export const workspaceUrl = (path: string, tab: 'files' | 'running' = 'files') => `${path ? `/browse?path=${encodeURIComponent(path)}` : '/'}${tab === 'running' ? `${path ? '&' : '?'}view=running` : ''}`
const typeFor = (entry: Entry): DashboardEntry['kind'] => entry.type === 'directory' ? entry.project_id ? 'project' : 'folder' : entry.name.toLowerCase().endsWith('.ipynb') ? 'notebook' : entry.name.toLowerCase().endsWith('.py') ? 'python' : /\.(md|markdown)$/i.test(entry.name) ? 'markdown' : /\.(txt|csv|json|ya?ml|toml|ini|cfg|log|rst|sql|sh|js|ts|css|html|xml)$/i.test(entry.name) ? 'text' : 'file'
function pendingOperation(): Operation | null {
  try { return JSON.parse(localStorage.getItem(pendingKey) ?? 'null') } catch { return null }
}
export function PhysicalWorkspace({ path, tab, navigate }: { path: string; tab: 'files' | 'running'; navigate: Navigate }) {
  const data = useDashboardData(tab === 'running')
  const [items, setItems] = useState<Entry[]>([]), [cursor, setCursor] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0), [busy, setBusy] = useState(false), [pending, setPending] = useState(pendingOperation)
  const [kind, setKind] = useState<Operation['kind']>('notebook'), [name, setName] = useState('')
  const dialog = useRef<HTMLDialogElement>(null), lock = useRef(false)
  useEffect(() => {
    let alive = true
    setLoading(true); setLoaded(false); setError(''); setItems([]); setCursor(null)
    void request<Listing>(`/workspace?path=${encodeURIComponent(path)}`).then(page => {
      if (alive) { setItems(page.items); setCursor(page.next_cursor); setLoaded(true) }
    }).catch(e => { if (alive) setError(String(e)) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [path, refresh])
  const perform = async (operation: Operation) => {
    if (lock.current) return
    lock.current = true; setBusy(true); setNotice('')
    try {
      const endpoint = { notebook: 'notebooks', open: 'notebooks/open', project: 'projects', folder: 'folders', text: 'files' }[operation.kind]
      const result = await request<{ notebook_id?: string }>(`/workspace/${endpoint}`, json('POST', { path: operation.path, operation_id: operation.operation_id }))
      localStorage.removeItem(pendingKey); setPending(null)
      dialog.current?.close()
      if (result.notebook_id) {
        if (!await navigate(`/notebooks/${result.notebook_id}`)) setNotice('Notebook saved but the editor could not open. Open it from its directory; do not create it again.')
      } else setRefresh(value => value + 1)
    } catch (e) { setNotice(`Operation requires review or exact replay: ${String(e)}`) }
    finally { lock.current = false; setBusy(false) }
  }
  const start = (operation: Operation) => {
    if (pendingOperation() || lock.current || localStorage.getItem('europa.workspace.create.pending') || localStorage.getItem('europa.workspace.pending')) { setNotice('Resolve the pending workspace operation before creating or associating another file.'); return }
    try { localStorage.setItem(pendingKey, JSON.stringify(operation)); setPending(operation); void perform(operation) }
    catch { setNotice('Browser operation storage unavailable. No request was sent.') }
  }
  const open = (entry: DashboardEntry) => {
    const target = [path, entry.name].filter(Boolean).join('/')
    if (entry.kind === 'project' || entry.kind === 'folder') void navigate(workspaceUrl(target))
    else if (entry.kind === 'notebook') start({ kind: 'open', path: target, operation_id: crypto.randomUUID() })
    else if (entry.kind === 'file') setNotice('This file type is not supported by the text editor.')
    else void navigate(`/text?path=${encodeURIComponent(target)}`)
  }
  const more = async () => {
    if (!cursor || loading) return
    setLoading(true)
    try {
      const page = await request<Listing>(`/workspace?path=${encodeURIComponent(path)}&cursor=${encodeURIComponent(cursor)}`)
      setItems(previous => [...previous, ...page.items]); setCursor(page.next_cursor)
    } catch (e) { setNotice(String(e)) } finally { setLoading(false) }
  }
  return <><Dashboard path={path ? path.split('/') : []} tab={tab} onTab={value => void navigate(workspaceUrl(path, value))} onHome={() => void navigate('/')} onDirectory={parts => void navigate(workspaceUrl(parts.join('/')))} onCatalog={value => void navigate(`/${value}`)}
    entries={items.map(entry => ({ name: entry.name, kind: typeFor(entry), modified: Number.isFinite(entry.modified) ? new Date(entry.modified * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : null, size: entry.size === null ? null : entry.size < 1024 ? `${entry.size} B` : `${(entry.size / 1024).toFixed(1)} KB` }))}
    sessions={data.sessions.map(item => ({ ...item, activity: item.last_activity === null ? null : new Date(item.last_activity * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }), observedAt: item.observed_at }))} recents={data.recent.map(item => ({ ...item, openedAt: item.opened_at }))} recentError={data.recentError} onRecentMenu={data.refreshRecent}
    onSession={id => void navigate(`/notebooks/${id}`)} onRecent={id => void navigate(`/notebooks/${id}`)} sessionError={data.sessionError} sessionNotice={data.sessionNotice} onSessionsMore={data.next} onSessionsPrevious={data.previous} loading={tab === 'files' ? loading : data.loading} error={tab === 'files' ? error : undefined} onOpen={open}
    actionsDisabled={busy || !!pending || !loaded || !!error || loading}
    onRefresh={() => { data.refreshRecent(); if (tab === 'running') data.refreshSessions(); else setRefresh(value => value + 1) }} onMore={cursor ? () => void more() : undefined}
    notice={<>{notice && <p role="alert">{notice}</p>}{pending && <p role="status">Pending {pending.kind}: {pending.path}. <button disabled={busy} onClick={() => void perform(pending)}>Retry exact operation</button></p>}</>}
    onCreate={value => { setKind(value); setName(''); setNotice(''); dialog.current?.showModal() }} />
    <dialog ref={dialog} className="dashboard-dialog" aria-labelledby="workspace-create-title"><form onSubmit={event => {
      event.preventDefault()
      let value = name.trim()
      if (!value || value.startsWith('.') || /[/\\]/.test(value)) { setNotice('Choose a plain file or folder name.'); return }
      if (kind === 'notebook' && !value.toLowerCase().endsWith('.ipynb')) value += '.ipynb'
      if (kind === 'text' && value.toLowerCase().endsWith('.ipynb')) { setNotice('Use New Notebook to create .ipynb files.'); return }
      start({ kind, path: [path, value].filter(Boolean).join('/'), operation_id: crypto.randomUUID() })
    }}><h2 id="workspace-create-title">New {kind === 'text' ? 'text file' : kind}</h2><p>Home{path ? ` / ${path.split('/').join(' / ')}` : ''}</p><label>Name<input autoFocus required value={name} disabled={busy || !!pending} onChange={event => setName(event.target.value)} /></label>{notice && <p role="alert">{notice}</p>}<button type="button" onClick={() => dialog.current?.close()}>Cancel</button> <button type="submit" disabled={busy || !!pending}>Create</button></form></dialog>
  </>
}
