import { useEffect, useRef, useState } from 'react'

type Draft = { content: string; source: string; revision: number }
export const commentDraftKey = (notebook: string, project: string, cell: string, parent: string | null) =>
  `europa.comment.draft.${JSON.stringify([notebook, project, cell, parent])}`

function read(key: string): Draft | null {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') as Draft | null } catch { return null }
}

export function clearSentCommentDraft(notebook: string, project: string, cell: string, parent: string | null, content: string) {
  const key = commentDraftKey(notebook, project, cell, parent)
  if (read(key)?.content === content) localStorage.removeItem(key)
}

export function InlineCommentComposer({ notebook, project, cell, parent, cellLabel, source, revision, invalid, busy, pending, onCancel, onSend }: {
  notebook: string; project: string; cell: string; parent: string | null; cellLabel?: string; source: string; revision?: number
  invalid: boolean; busy: boolean; pending: boolean; onCancel: () => void
  onSend: (content: string, reviewedRevision: number, stillCurrent: () => boolean) => Promise<boolean>
}) {
  const key = commentDraftKey(notebook, project, cell, parent)
  const [draft, setDraft] = useState<Draft>(() => read(key) ?? { content: '', source, revision: revision ?? -1 })
  const input = useRef<HTMLTextAreaElement>(null)
  const wasPending = useRef(pending)
  useEffect(() => { input.current?.focus(); input.current?.scrollIntoView?.({ block: 'nearest' }) }, [])
  useEffect(() => {
    if (wasPending.current && !pending && !localStorage.getItem(key)) setDraft(current => current.content ? { ...current, content: '' } : current)
    wasPending.current = pending
  }, [pending, key])
  const update = (content: string) => {
    const next = { ...draft, content }
    setDraft(next); localStorage.setItem(key, JSON.stringify(next))
  }
  const changed = !parent && (draft.source !== source || revision === undefined || draft.revision !== revision)
  const blocked = invalid || changed
  const submit = async () => {
    const content = draft.content
    if (!content.trim() || blocked || busy || pending) return
    const stillCurrent = () => read(key)?.content === content
    if (await onSend(content, draft.revision, stillCurrent)) {
      clearSentCommentDraft(notebook, project, cell, parent, content)
      setDraft(current => current.content === content ? { ...current, content: '' } : current)
    }
  }
  return <form className="inline-comment-composer" onSubmit={event => { event.preventDefault(); void submit() }} onKeyDown={event => event.stopPropagation()}>
    <label htmlFor={`comment-${key}`}>{parent ? 'Reply' : 'Comment'} · {cellLabel ?? cell}</label>
    <textarea id={`comment-${key}`} ref={input} rows={4} maxLength={32000} value={draft.content} onChange={event => update(event.target.value)} onKeyDown={event => {
      event.stopPropagation()
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() }
    }} />
    {blocked && <p role="alert" className="alert">{parent ? 'Original comment thread is unavailable in this project and notebook.' : 'Cell is missing, changed, or has an unsaved/conflicted draft. Save and review this cell before posting; this draft will stay with its original cell.'}</p>}
    {!parent && changed && !invalid && revision !== undefined && <button type="button" onClick={() => { const next = { ...draft, source, revision }; setDraft(next); localStorage.setItem(key, JSON.stringify(next)) }}>Use current saved cell</button>}
    <div className="inline-comment-actions"><button type="submit" disabled={blocked || busy || pending || !draft.content.trim()}>{parent ? 'Post reply' : 'Post comment'}</button><button type="button" onClick={onCancel}>Cancel</button></div>
  </form>
}
