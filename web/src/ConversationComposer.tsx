import { useEffect, useRef, useState } from 'react'

export type CellReference = { id: string; revision: number; source: string }
type Draft = { content: string; reply: string | null; needsInput: boolean; reference: CellReference | null }
const empty = (): Draft => ({ content: '', reply: null, needsInput: false, reference: null })
export const draftKey = (notebook: string, conversation: string) => `europa.message.draft.${notebook}.${conversation}`
function recover(notebook: string, conversation: string): Draft {
  const key = draftKey(notebook, conversation)
  if (localStorage.getItem(key)) return read(key)
  const prefix = `europa.message.draft.${notebook}.`
  const candidates = Object.keys(localStorage).filter(k => k.startsWith(prefix) && !k.endsWith('.origin') && k !== key && read(k).content)
  if (candidates.length !== 1) return empty()
  const draft = read(candidates[0])
  localStorage.setItem(key, JSON.stringify(draft))
  localStorage.setItem(`${key}.origin`, candidates[0])
  return draft
}
function read(key: string): Draft {
  try { return { ...empty(), ...JSON.parse(localStorage.getItem(key) ?? '{}') } } catch { return empty() }
}
export function clearSentDraft(notebook: string, conversation: string, content: string) {
  const key = draftKey(notebook, conversation)
  if (read(key).content === content) {
    localStorage.removeItem(key)
    const origin = localStorage.getItem(`${key}.origin`)
    if (origin?.startsWith(`europa.message.draft.${notebook}.`) && read(origin).content === content) localStorage.removeItem(origin)
    localStorage.removeItem(`${key}.origin`)
  }
}

export function ConversationComposer({ notebookId, conversationId, cells, drafts, conflict, revision, selectedCell, reference, reply, onReplyConsumed, onReferenceRemoved, busy, pending, onSend }: {
  notebookId: string; conversationId: string; cells: { id: string; source: string }[]; drafts: string[]; conflict: boolean; revision?: number; selectedCell?: string
  reference: CellReference | null; reply: string | null; onReplyConsumed: () => void; onReferenceRemoved?: () => void; busy: boolean; pending: boolean
  onSend: (body: object, content: string) => Promise<boolean>
}) {
  const key = draftKey(notebookId, conversationId)
  const [draft, setDraft] = useState(() => recover(notebookId, conversationId))
  const wasPending = useRef(pending)
  useEffect(() => { setDraft(recover(notebookId, conversationId)) }, [key])
  useEffect(() => { if (reference) setDraft(d => ({ ...d, reference })) }, [reference])
  useEffect(() => { if (reply) setDraft(d => ({ ...d, reply })) }, [reply])
  useEffect(() => { if (wasPending.current && !pending && !localStorage.getItem(key)) setDraft(empty()); wasPending.current = pending }, [pending, key])
  useEffect(() => { if (draft.content || draft.reply || draft.reference || draft.needsInput) localStorage.setItem(key, JSON.stringify(draft)); else localStorage.removeItem(key) }, [key, draft])
  const attached = draft.reference && cells.find(c => c.id === draft.reference?.id)
  const invalid = !!draft.reference && (!attached || drafts.includes(draft.reference.id) || conflict || attached.source !== draft.reference.source || revision === undefined || revision < draft.reference.revision)
  const set = (next: Draft) => { setDraft(next); localStorage.setItem(key, JSON.stringify(next)) }
  const send = async () => {
    if (!draft.content.trim() || invalid || busy || pending) return
    const snapshot = draft
    const body = { content: snapshot.content, parent_id: snapshot.reply, state: snapshot.needsInput ? 'needs_input' : 'published', notebook_id: notebookId, ...(snapshot.reference ? { cell_id: snapshot.reference.id, document_revision: snapshot.reference.revision } : {}) }
    if (await onSend(body, snapshot.content)) { clearSentDraft(notebookId, conversationId, snapshot.content); setDraft(current => current.content === snapshot.content ? empty() : current); onReplyConsumed(); onReferenceRemoved?.() }
  }
  return <form className="message-composer" onSubmit={event => { event.preventDefault(); void send() }}>
    <div className="composer-context">
      {draft.reference ? <span className="reference-chip"><button type="button" disabled={!attached} onClick={() => document.getElementById(`cell-${draft.reference?.id}`)?.scrollIntoView({ block: 'nearest' })}>{attached ? `Cell ${cells.indexOf(attached) + 1}` : 'Missing cell'} · {draft.reference.source.slice(0, 90) || '(empty)'} · saved revision {draft.reference.revision}</button><button type="button" aria-label="Remove cell reference" onClick={() => { set({ ...draft, reference: null }); onReferenceRemoved?.() }}>×</button></span> : <button type="button" disabled={!selectedCell || !cells.some(c => c.id === selectedCell)} onClick={() => { const cell = cells.find(c => c.id === selectedCell); if (cell && revision !== undefined) set({ ...draft, reference: { id: cell.id, source: cell.source, revision } }) }}>Attach selected cell</button>}
      {draft.reply && <span>Reply to <a href={`#message-${draft.reply}`}>{draft.reply.slice(0, 8)}</a> <button type="button" onClick={() => { set({ ...draft, reply: null }); onReplyConsumed() }}>Cancel reply</button></span>}
    </div>
    {invalid && <p role="alert" className="alert">Cell reference is missing, changed, or has an unsaved/conflicted draft. Save and review the cell, or remove the reference before sending.</p>}
    <label htmlFor="conversation-message" className="visually-hidden">Message</label><div className="composer-input"><textarea id="conversation-message" placeholder="Write a message..." required maxLength={32000} value={draft.content} onChange={event => set({ ...draft, content: event.target.value })} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} /><button aria-label="Send" title="Send" disabled={busy || pending || invalid || !draft.content.trim()}>➤</button></div><details className="composer-options"><summary>Options</summary><label><input type="checkbox" checked={draft.needsInput} onChange={event => set({ ...draft, needsInput: event.target.checked })} />Needs input</label></details>
  </form>
}
