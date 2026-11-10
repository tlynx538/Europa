import { useEffect, useState } from 'react'
import { json, request } from './api'
import type { Navigate } from './Navigation'

type Entry = { name: string; type: 'directory' | 'file'; size: number | null; modified: number }
type Listing = { path: string; items: Entry[]; next_cursor: string | null }
export const browseUrl = (path: string) => `/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`
const mutationKey = 'europa.workspace.create.pending'
type PendingCreate = { kind: 'folders' | 'files'; path: string; operation_id: string }
function pendingCreate(): PendingCreate | null {
  try { return JSON.parse(localStorage.getItem(mutationKey) ?? 'null') as PendingCreate | null } catch { return null }
}

export function WorkspaceBrowse({ navigate, path = '' }: { navigate: Navigate; path?: string }) {
  const [items, setItems] = useState<Entry[]>([]), [cursor, setCursor] = useState<string | null>(null)
  const [error, setError] = useState(''), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(pendingCreate())
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let live = true
    setItems([]); setCursor(null); setError(''); setLoaded(false)
    void request<Listing>(`/workspace?path=${encodeURIComponent(path)}`).then(page => {
      if (live) { setItems(page.items); setCursor(page.next_cursor); setLoaded(true) }
    }).catch(e => { if (live) setError(String(e)) })
    return () => { live = false }
  }, [path, refresh])
  const retry = async (body: PendingCreate) => {
    setBusy(true); setError('')
    try {
      await request(`/workspace/${body.kind}`, json('POST', { path: body.path, operation_id: body.operation_id }))
      localStorage.removeItem(mutationKey); setPending(null); setRefresh(value => value + 1)
    } catch (e) { setError(`Creation outcome requires inspection or exact replay: ${String(e)}`) }
    finally { setBusy(false) }
  }
  const create = (kind: PendingCreate['kind']) => {
    if (busy || pending || localStorage.getItem('europa.workspace.pending')) return
    const name = prompt(kind === 'folders' ? 'New folder name' : 'New text file name')?.trim()
    if (!name) return
    if (name.startsWith('.') || name === '..' || name.includes('/') || name.includes('\\') || (kind === 'files' && name.toLowerCase().endsWith('.ipynb'))) { setError('Choose a plain folder or text file name; notebook creation is not available yet.'); return }
    const body = { kind, path: [...segments, name].join('/'), operation_id: crypto.randomUUID() }
    localStorage.setItem(mutationKey, JSON.stringify(body)); setPending(body); void retry(body)
  }
  const segments = path ? path.split('/') : []
  const open = (entry: Entry) => {
    const target = [...segments, entry.name].join('/')
    if (entry.type === 'directory') void navigate(browseUrl(target))
    else if (entry.name.toLowerCase().endsWith('.ipynb')) setError('Physical notebook opening is not available yet. No notebook was created or executed.')
    else void navigate(`/text?path=${encodeURIComponent(target)}`)
  }
  const more = async () => {
    if (!cursor || busy) return
    setBusy(true)
    try {
      const page = await request<Listing>(`/workspace?path=${encodeURIComponent(path)}&cursor=${encodeURIComponent(cursor)}`)
      setItems(previous => [...previous, ...page.items]); setCursor(page.next_cursor); setError('')
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <main className="browser-page workspace-browse"><nav aria-label="Workspace path" className="browser-breadcrumb"><button onClick={() => void navigate(browseUrl(''))}>Workspace root</button>{segments.map((segment, i) => <span key={i}> / <button onClick={() => void navigate(browseUrl(segments.slice(0, i + 1).join('/')))}>{segment}</button></span>)}</nav>
    <div className="browser-heading"><h1>Browse files</h1><button onClick={() => setRefresh(value => value + 1)}>Refresh</button></div>
    <div className="workspace-actions"><button disabled={busy || !!pending} onClick={() => create('folders')}>New folder</button><button disabled={busy || !!pending} onClick={() => create('files')}>New text file</button></div>
    {pending && <p role="status">Creation outcome unknown. Inspect the folder or retry the exact operation. <button disabled={busy} onClick={() => void retry(pending)}>Retry exact creation</button></p>}
    {error && <p role="alert" className="alert">{error}</p>}
    <ul className="browser-list">{items.map(entry => <li key={entry.name}><button onClick={() => open(entry)}><span className={`browser-icon browser-icon-${entry.type === 'directory' ? 'folder' : 'file'}`} aria-hidden="true" />{entry.name}</button><small>{entry.type === 'directory' ? 'Folder' : `${entry.size ?? 0} bytes`}</small></li>)}</ul>
    {loaded && !error && !items.length && <p>Empty folder</p>}
    {cursor && <button disabled={busy} onClick={() => void more()}>Load more</button>}
  </main>
}
