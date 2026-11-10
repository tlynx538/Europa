import { useEffect, useRef, useState } from 'react'
import { agentControl, agentDecision, agentEvents, liveRun, stateLabel, type AgentApproval, type AgentEvent, type AgentRun } from './agentApi'

const labels: Record<string,string> = {run_queued:'Request queued',run_running:'Run started',model_requested:'Requesting model response',model_responded:'Model response received',tool_proposed:'Action proposed',approval_requested:'Approval requested',approval_granted:'Approved by owner',approval_denied:'Denied by owner',approval_expired:'Approval expired',tool_started:'Action started',tool_completed:'Action completed',tool_failed:'Action refused or failed',tool_uncertain:'Outcome could not be verified',handoff_saved:'Notebook and handoff saved',reply_saved:'Reply saved',run_completed:'Run completed',run_cancelled:'Run cancelled',run_interrupted:'Executor interrupted',run_cancelling:'Stop requested'}
const text = (value:unknown) => typeof value === 'string' ? value : ''

function Proposal({approval}: {approval:AgentApproval}) {
  if (approval.tool==='create_workspace_notebook') return <dl className="agent-proposal"><dt>Project directory</dt><dd>{text(approval.target.path)}</dd><dt>Notebook</dt><dd>{text(approval.target.notebook_path)}</dd><dt>Objective</dt><dd>{text(approval.arguments.objective)}</dd></dl>
  if (approval.tool==='save_notebook_plan') {
    const tasks = approval.arguments.tasks as {id:string;title:string;acceptance_criteria:string[];dependencies:string[];cell_id?:string}[]
    return <div className="agent-plan"><p>{text(approval.arguments.objective)}</p>
      <small>Notebook revision {String(approval.arguments.revision)} · {approval.arguments.roadmap_id ? `Replace plan at revision ${approval.arguments.roadmap_revision}` : 'Create roadmap'}</small>
      <ol>{tasks.map(task=><li key={task.id}><strong>{task.title}</strong><ul>{task.acceptance_criteria.map((criterion,index)=><li key={index}>{criterion}</li>)}</ul>
        {task.dependencies.length>0&&<small>After {task.dependencies.map(id=>tasks.find(t=>t.id===id)?.title??id).join(', ')}</small>}
        {task.cell_id&&<a href={`#cell-${task.cell_id}`}>Linked cell</a>}</li>)}</ol></div>
  }
  return <pre className="agent-payload">{JSON.stringify(approval.arguments,null,2)}</pre>
}

export function AgentActivity({ run, onChange }: {run:AgentRun;onChange:()=>void}) {
  const [events,setEvents]=useState<AgentEvent[]>([]), [open,setOpen]=useState(liveRun(run.state)), [error,setError]=useState(''), [busy,setBusy]=useState(false)
  const [refresh,setRefresh]=useState(0), [more,setMore]=useState(false)
  const cursor=useRef(0), lock=useRef(false)
  const attempts=useRef(new Map<string,string>())
  useEffect(()=>{setOpen(liveRun(run.state))},[run.state])
  useEffect(()=>{
    if(!open&&!liveRun(run.state))return
    const controller=new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let reads=0
    async function read() {
      try {
        const page=await agentEvents(run.id,cursor.current,controller.signal)
        if(controller.signal.aborted)return
        cursor.current=page.next_after;setMore(page.has_more)
        setEvents(previous=>[...previous,...page.items.filter(item=>!previous.some(e=>e.sequence===item.sequence))].slice(-100))
        if(open&&liveRun(run.state)&&++reads<120)timer=setTimeout(()=>void read(),1250)
      } catch {if(!controller.signal.aborted)setError('Activity unavailable. Refresh to reconnect.')}
    }
    void read()
    return ()=>{controller.abort();clearTimeout(timer)}
  },[run.id,run.state,open,refresh])
  async function act(key:string, action:(operation:string)=>Promise<unknown>) {
    if(lock.current)return
    lock.current=true;setBusy(true);setError('')
    const id=attempts.current.get(key)??crypto.randomUUID();attempts.current.set(key,id)
    try {await action(id);onChange()}
    catch (e) {setError(String(e));onChange()}
    finally {lock.current=false;setBusy(false)}
  }
  const review=run.approval
  return <section className="agent-activity" aria-label={`${run.role==='coding_agent'?'Coding Agent':'Coordinator'} activity`}>
    <div className="agent-run-heading"><button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{run.role==='coding_agent'?'Coding Agent':'Coordinator'} · {stateLabel[run.state]}</button>
      {liveRun(run.state)&&run.state!=='cancelling'&&<button type="button" disabled={busy} onClick={()=>void act('cancel',id=>agentControl(run.id,'cancel',id))}>Stop run</button>}
      {run.state==='interrupted'&&<button type="button" disabled={busy} onClick={()=>void act('resume',id=>agentControl(run.id,'resume',id))}>Resume run</button>}
    </div>
    {review&&<div className="agent-review" aria-label="Pending approval"><h3>Review proposed action</h3><Proposal approval={review}/>
      <div className="agent-buttons"><button type="button" disabled={busy} onClick={()=>void act(review.id+':grant',id=>agentDecision(run.id,review,'granted',id))}>Approve exact action</button>
      <button type="button" disabled={busy} onClick={()=>void act(review.id+':deny',id=>agentDecision(run.id,review,'denied',id))}>Deny</button></div></div>}
    {run.error&&<p className="agent-error" role="alert">{run.error}</p>}
    {error&&<p className="agent-error" role="alert">{error} <button type="button" onClick={()=>{setError('');setRefresh(v=>v+1);onChange()}}>Refresh activity</button></p>}
    {open&&<><ol className="agent-events">{events.map(event=><li key={event.sequence}><span>{labels[event.kind]??event.kind.replaceAll('_',' ')}</span>{text(event.data.message)&&<p>{text(event.data.message)}</p>}{text(event.data.error)&&<p>{text(event.data.error)}</p>}</li>)}</ol>{more&&<button type="button" onClick={()=>setRefresh(v=>v+1)}>Load more activity</button>}</>}
  </section>
}
