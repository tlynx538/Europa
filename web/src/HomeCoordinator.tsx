import { useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { agentStart, liveRun } from './agentApi'
import { AgentActivity } from './AgentActivity'
import { useAgentFeed } from './useAgentFeed'
import type { Navigate } from './Navigation'
import './agents.css'

type Pending = {operation_id:string;scope:{kind:'workspace';directory:string};mode:'coordinate';content:string}
const key = 'europa.agent.home.pending'
function pendingSend(): Pending | null {
  try { return JSON.parse(localStorage.getItem(key)??'null') } catch { return null }
}

export function HomeCoordinator({path,navigate,onCreated}: {path:string;navigate:Navigate;onCreated:()=>void}) {
  const [open,setOpen] = useState(false)
  const [draft,setDraft] = useState('')
  const [pending,setPending] = useState(pendingSend)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const lock = useRef(false)
  const seen = useRef(new Set<string>())
  const feed = useAgentFeed({kind:'workspace',directory:path},open)
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal()
    if (!open && dialog.current?.open) dialog.current.close()
  },[open])
  useEffect(() => {
    if (feed.handoffs.some(item=>!seen.current.has(item.id))) {
      for (const item of feed.handoffs) seen.current.add(item.id)
      onCreated()
    }
  },[feed.handoffs,onCreated])
  async function send(body:Pending) {
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    try {
      localStorage.setItem(key,JSON.stringify(body))
      setPending(body)
      await agentStart(body)
      localStorage.removeItem(key)
      setPending(null)
      setDraft('')
      feed.reload()
    } catch (e) {
      if (e instanceof ApiError && e.status < 500) {
        localStorage.removeItem(key)
        setPending(null)
        setDraft(body.content)
      }
      setError(String(e))
    } finally { lock.current=false;setBusy(false) }
  }
  const rendered = new Set(feed.messages.map(message=>message.id))
  return <>
    <button type="button" className="agent-trigger" ref={trigger} onClick={()=>setOpen(true)}>Ask Europa…</button>
    <dialog className="agent-drawer" aria-labelledby="home-agent-title" ref={dialog} onClose={()=>{setOpen(false);trigger.current?.focus()}}>
      {open && <div className="agent-drawer-layout">
        <header><h2 id="home-agent-title">Ask Europa</h2><button type="button" onClick={()=>setOpen(false)}>Close</button></header>
        {feed.config?.test_provider && <p className="agent-provider">Deterministic test provider</p>}
        <div className="agent-thread">
          {feed.before && <button type="button" onClick={()=>void feed.loadOlder()}>Load earlier messages</button>}
          {feed.messages.map(message=><div key={message.id}>
            <article className="agent-message" data-author={message.data.author_role}><strong>{message.data.author_role==='user'?'You':'Coordinator'}</strong><p>{message.data.content}</p></article>
            {feed.runs.filter(run=>run.messages.some(link=>link.purpose==='user'&&link.message_id===message.id)).map(run=><AgentActivity key={run.id} run={run} onChange={feed.reload}/>)}
          </div>)}
          {feed.runs.filter(run=>(liveRun(run.state)||run.state==='needs_reconciliation')&&!run.messages.some(link=>link.purpose==='user'&&rendered.has(link.message_id))).map(run=><AgentActivity key={run.id} run={run} onChange={feed.reload}/>)}
          {feed.handoffs.map(item=><div key={item.id} className="agent-handoff"><span>{item.path}</span><button type="button" onClick={()=>{void navigate('/notebooks/'+item.notebook_id).then(ok=>{if(ok)setOpen(false)})}}>Open notebook</button></div>)}
        </div>
        {feed.error&&<p role="alert" className="agent-error">{feed.error} <button type="button" onClick={feed.reload}>Refresh</button></p>}
        {error&&<p role="alert" className="agent-error">{error}</p>}
        {pending&&<p className="agent-provider">A send is awaiting confirmation. <button type="button" disabled={busy} onClick={()=>void send(pending)}>Retry exact message</button></p>}
        {feed.config&&!feed.config.enabled&&<p className="agent-provider">{feed.config.error||'Agent provider is disabled.'}</p>}
        <form onSubmit={event=>{event.preventDefault();if(draft.trim()&&feed.config?.enabled&&!pending&&!busy)void send({operation_id:crypto.randomUUID(),scope:{kind:'workspace',directory:path},mode:'coordinate',content:draft.trim()})}}>
          <label>Message to Europa<textarea value={draft} onChange={event=>setDraft(event.target.value)} maxLength={8000} rows={3} disabled={!!pending}/></label>
          <button type="submit" disabled={!feed.config?.enabled||!draft.trim()||!!pending||busy}>Send message</button>
        </form>
      </div>}
    </dialog>
  </>
}
