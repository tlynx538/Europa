import { useEffect, useState } from 'react'
import { json, request } from './api'
import type { Navigate } from './Navigation'
import { browseUrl } from './WorkspaceBrowse'

type TextDocument = { path: string; content: string; revision: string }
type PendingSave = { path: string; content: string; revision: string; operation_id: string }
const draftKey = (path: string) => `europa.workspace.draft.${path}`
const pendingKey = 'europa.workspace.pending'
export function pendingWorkspaceSave(): PendingSave | null {
  try { return JSON.parse(localStorage.getItem(pendingKey) ?? 'null') as PendingSave | null } catch { return null }
}
export function TextFileEditor({ path, navigate }: { path: string; navigate: Navigate }) {
  const [document, setDocument] = useState<TextDocument | null>(null), [content, setContent] = useState('')
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pending, setPending] = useState(pendingWorkspaceSave())
  useEffect(() => {
    let live = true
    setDocument(null); setError('')
    void request<TextDocument>(`/workspace/text?path=${encodeURIComponent(path)}`).then(result => {
      if (!live) return
      setDocument(result)
      setContent(localStorage.getItem(draftKey(path)) ?? result.content)
    }).catch(e => { if (live) setError(String(e)) })
    return () => { live = false }
  }, [path])
  const retry = async (body: PendingSave) => {
    setBusy(true); setError('')
    try {
      const saved = await request<TextDocument>('/workspace/text/save', json('POST', body))
      localStorage.removeItem(pendingKey); setPending(null); setDocument(saved)
      if (content === body.content && body.path === path) localStorage.removeItem(draftKey(path))
      else if (body.path === path) setError('Save completed, but a newer draft remains for review.')
    } catch (e) { setError(`Save outcome requires review or exact replay: ${String(e)}`) }
    finally { setBusy(false) }
  }
  const save = () => {
    if (!document || busy || pendingWorkspaceSave() || content === document.content) return
    const body = { path, content, revision: document.revision, operation_id: crypto.randomUUID() }
    localStorage.setItem(pendingKey, JSON.stringify(body)); setPending(body)
    void retry(body)
  }
  return <main className="browser-page text-file-editor"><button onClick={() => void navigate(browseUrl(path.split('/').slice(0, -1).join('/')))}>Back to files</button><h1>{path.split('/').at(-1)}</h1>
    {error && <p role="alert" className="alert">{error}</p>}
    {pending && <p role="status">A save has an unknown outcome. Retry the exact operation; do not overwrite this file yet. <button disabled={busy} onClick={() => void retry(pending)}>Retry exact save</button></p>}
    {document && <><p>{content !== document.content ? 'Unsaved browser draft' : 'Saved file'} · Revision {document.revision.slice(0, 12)}</p><label htmlFor="workspace-text">File contents</label><textarea id="workspace-text" aria-label="File contents" rows={20} spellCheck={false} value={content} onChange={e => { const value = e.target.value; setContent(value); localStorage.setItem(draftKey(path), value) }} /><button disabled={busy || !!pending || content === document.content} onClick={save}>Save text file</button></>}
  </main>
}
