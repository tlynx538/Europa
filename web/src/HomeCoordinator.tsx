import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { all, type Message, type RecordData } from './applicationApi'
import { agentStatus, homeConversation, newHomeConversation, pendingAgent, postHomeMessage, providerLabel, retryAgent, scopes, startRun, unknownOutcome, type AgentStatus, type RunView } from './agentApi'
import { AgentRunPanel, useAgentRuns } from './AgentRun'
import type { Navigate } from './Navigation'
import { consumeKickoffFocus, requestKickoffFocus } from './kickoffFocus'
import './agent.css'

const openKey = 'europa.agent.home.open'
export const homeDraftKey = (conversation: string) => `europa.agent.draft.${conversation}`
const UNKNOWN = 'Starting the Coordinator has an unknown outcome.'

export function HomeCoordinator({ navigate, onWorkspaceChanged }: { navigate: Navigate; onWorkspaceChanged?: () => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const enabled = status?.enabled === true
  useEffect(() => {
    let alive = true
    agentStatus().then(value => { if (alive) setStatus(value) }).catch(() => { /* unavailable: render nothing */ })
    return () => { alive = false }
  }, [])
  if (!enabled || !status) return null
  return <CoordinatorPanel status={status} navigate={navigate} onWorkspaceChanged={onWorkspaceChanged} />
}

function CoordinatorPanel({ status, navigate, onWorkspaceChanged }: { status: AgentStatus; navigate: Navigate; onWorkspaceChanged?: () => void }) {
  const [conversation, setConversation] = useState<string>()
  const [messages, setMessages] = useState<RecordData<Message>[]>([])
  const [draft, setDraft] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [unknown, setUnknown] = useState(false)
  const [open, setOpen] = useState(() => localStorage.getItem(openKey) !== '0')
  const thread = useRef<HTMLDivElement>(null), lock = useRef(false), created = useRef<Set<string> | null>(null)
  const workspaceChanged = useRef(onWorkspaceChanged); workspaceChanged.current = onWorkspaceChanged

  useEffect(() => {
    let alive = true
    homeConversation().then(record => { if (alive) setConversation(record.id) }).catch(e => { if (alive) setError(`Coordinator unavailable: ${String(e)}`) })
    return () => { alive = false }
  }, [])
  const loadMessages = useCallback(async (id = conversation) => {
    if (!id) return
    try { setMessages(await all<Message>(`/conversations/${encodeURIComponent(id)}/messages`)) }
    catch (e) { setError(`Messages unavailable: ${String(e)}`) }
  }, [conversation])
  useEffect(() => {
    if (!conversation) return
    created.current = null
    setDraft(localStorage.getItem(homeDraftKey(conversation)) ?? '')
    setUnknown(!!pendingAgent(scopes.message(conversation)) || !!pendingAgent(scopes.run(conversation)) || !!pendingAgent(scopes.home))
    void loadMessages(conversation)
  }, [conversation, loadMessages])

  const onRunsChanged = useCallback((runs: RunView[]) => {
    const succeeded = runs.flatMap(run => run.tool_calls).filter(call => call.name === 'create_project_notebook' && call.state === 'succeeded').map(call => call.id)
    const known = created.current
    created.current = new Set(succeeded)
    if (known && succeeded.some(id => !known.has(id))) workspaceChanged.current?.()
    void loadMessages()
  }, [loadMessages])
  const { runs, error: runError, refresh } = useAgentRuns(conversation, true, onRunsChanged)

  useLayoutEffect(() => { if (thread.current) thread.current.scrollTop = thread.current.scrollHeight }, [messages.length, runs.length, open])

  const updateDraft = (value: string) => {
    setDraft(value)
    if (!conversation) return
    if (value) localStorage.setItem(homeDraftKey(conversation), value); else localStorage.removeItem(homeDraftKey(conversation))
  }
  const begin = async (id: string, messageId: string) => {
    await startRun(id, messageId); setUnknown(false); refresh()
  }
  const fail = (e: unknown) => {
    if (unknownOutcome(e)) { setUnknown(true); setError('') } else { setUnknown(false); setError(e instanceof Error ? e.message : String(e)) }
  }
  const guarded = async (work: () => Promise<void>) => {
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try { await work() } catch (e) { fail(e) } finally { lock.current = false; setBusy(false) }
  }
  const send = () => guarded(async () => {
    const content = draft.trim()
    if (!content || !conversation) return
    const message = await postHomeMessage(conversation, content)
    if ((localStorage.getItem(homeDraftKey(conversation)) ?? '') === draft) updateDraft('')
    await loadMessages(conversation)
    await begin(conversation, message.id)
  })
  const retry = () => guarded(async () => {
    if (!conversation) return
    if (pendingAgent(scopes.message(conversation))) {
      const message = await retryAgent<RecordData<Message>>(scopes.message(conversation))
      const sent = message.data.content
      if ((localStorage.getItem(homeDraftKey(conversation)) ?? '').trim() === sent) updateDraft('')
      await loadMessages(conversation)
      await begin(conversation, message.id)
    } else if (pendingAgent(scopes.run(conversation))) {
      await retryAgent<RunView>(scopes.run(conversation)); setUnknown(false); refresh()
    } else if (pendingAgent(scopes.home)) {
      const record = await retryAgent<RecordData<{ title: string }>>(scopes.home)
      setUnknown(false); setMessages([]); setConversation(record.id)
    } else setUnknown(false)
  })
  const fresh = () => guarded(async () => {
    const record = await newHomeConversation()
    setMessages([]); setConversation(record.id)
  })

  return <details className="home-coordinator" open={open} onToggle={event => { const value = event.currentTarget.open; setOpen(value); localStorage.setItem(openKey, value ? '1' : '0') }}>
    <summary><span className="home-coordinator-title">Coordinator</span> <span className="home-provider">{providerLabel(status)}</span></summary>
    <div className="home-thread" ref={thread} role="log" aria-label="Coordinator conversation">
      {!messages.length && <p className="home-empty">Describe what you want to understand or build. The Coordinator proposes a project and notebook only when one helps, and creates nothing without your approval.</p>}
      {messages.map(message => {
        const author = message.data.author_role === 'user' ? 'user' : 'coordinator'
        return <div key={message.id} className="home-turn">
          <article className="home-message" data-author={author} id={`home-message-${message.id}`}>
            <h4>{author === 'user' ? 'You' : 'Coordinator'}</h4>
            <p>{message.data.content}</p>
            {author === 'coordinator' && message.data.notebook_id && <button type="button" onClick={() => { const id = message.data.notebook_id!; requestKickoffFocus(id); void navigate(`/notebooks/${id}`).then(moved => { if (!moved) consumeKickoffFocus() }) }}>Open notebook</button>}
          </article>
          {runs.filter(run => run.trigger_message_id === message.id).map(run => <AgentRunPanel key={run.id} run={run} onChanged={refresh} />)}
        </div>
      })}
    </div>
    {unknown && <p role="status" className="agent-warning">{UNKNOWN} <button type="button" disabled={busy} onClick={() => void retry()}>Retry</button></p>}
    {(error || runError) && <p role="alert" className="agent-error">{error || runError}</p>}
    <form className="home-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      <label htmlFor="home-coordinator-message" className="dashboard-sr">Message the Coordinator</label>
      <textarea id="home-coordinator-message" rows={2} maxLength={32000} placeholder="Ask the Coordinator…" value={draft} disabled={!conversation}
        onChange={event => updateDraft(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
      <div className="home-composer-buttons">
        <button type="submit" disabled={busy || unknown || !conversation || !draft.trim()}>Send</button>
        <button type="button" disabled={busy || unknown} onClick={() => void fresh()}>New</button>
      </div>
    </form>
  </details>
}
