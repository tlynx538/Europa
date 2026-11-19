import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { activeRunStates, cancelRun, conversationRuns, decide, pendingAgent, roadmapPreview, scopes, terminalRunStates, unknownOutcome, type ApprovalView, type RunView } from './agentApi'
import './agent.css'

export const pollDelay = (runs: RunView[]) =>
  runs.some(run => activeRunStates.includes(run.state)) ? 800 : runs.some(run => run.state === 'waiting_for_approval') ? 2500 : 4000

const signature = (runs: RunView[]) => JSON.stringify(runs.map(run => [run.id, run.state,
  run.tool_calls.map(call => [call.id, call.state]), run.approvals.map(approval => [approval.id, approval.state])]))

/** Poll a conversation's runs. `onChange` fires only when a run, tool call or approval state changes
 * (including the first load, when `previous` is null). */
export function useAgentRuns(conversationId: string | undefined, enabled: boolean,
  onChange?: (runs: RunView[], previous: RunView[] | null) => void) {
  const [runs, setRuns] = useState<RunView[]>([]), [error, setError] = useState('')
  const last = useRef<{ key: string; runs: RunView[] } | null>(null)
  const callback = useRef(onChange); callback.current = onChange
  const kick = useRef<() => void>(() => {})
  useEffect(() => {
    last.current = null; setRuns([]); setError('')
    if (!enabled || !conversationId) return
    let alive = true, timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      clearTimeout(timer)
      let next = last.current?.runs ?? []
      try {
        next = await conversationRuns(conversationId)
        if (!alive) return
        setError('')
        const key = signature(next)
        if (key !== last.current?.key) {
          const previous = last.current?.runs ?? null
          last.current = { key, runs: next }
          setRuns(next)
          callback.current?.(next, previous)
        }
      } catch (e) {
        if (alive) setError(`Agent status unavailable: ${String(e)}`)
      }
      if (alive) timer = setTimeout(() => void poll(), pollDelay(next))
    }
    kick.current = () => { if (alive) void poll() }
    void poll()
    return () => { alive = false; clearTimeout(timer); kick.current = () => {} }
  }, [conversationId, enabled])
  const refresh = useCallback(() => kick.current(), [])
  return { runs, error, refresh }
}

const toolLabels: Record<string, string> = {
  list_workspace: 'Looked at workspace files', list_projects: 'Listed projects', list_notebooks: 'Listed notebooks',
  read_notebook_summary: 'Read a notebook summary', create_project_notebook: 'Create project and notebook',
  read_notebook: 'Read the notebook', read_roadmap: 'Read the roadmap', read_execution: 'Read an execution receipt',
  propose_roadmap: 'Propose roadmap', ask_owner: 'Ask the owner', delegate_task: 'Assign to the Coding Agent',
  insert_code_cell: 'Insert code cell', insert_markdown_cell: 'Insert Markdown cell', replace_cell_source: 'Replace cell source',
  execute_cell: 'Run cell',
}
/** Cell-changing approvals: they bind a notebook revision and must not race the owner's unsaved edits. */
export const cellTools = ['insert_code_cell', 'insert_markdown_cell', 'replace_cell_source', 'execute_cell']
/** The open notebook's editing state, passed to approval cards in the notebook view only. */
export type EditGuard = { drafts: boolean; revision?: number }
export function approveBlocked(approval: ApprovalView, guard?: EditGuard) {
  if (!guard || !cellTools.includes(approval.tool)) return null
  if (guard.drafts) return 'Save or discard your edits first.'
  const bound = approval.preview.notebook_revision
  if (typeof bound === 'number' && guard.revision !== bound) return 'Notebook changed — the agent will re-propose.'
  return null
}
const textOf = (value: unknown) => typeof value === 'string' && value ? value : null

const callStates: Record<string, string> = {
  rejected: 'Not allowed', awaiting_approval: 'Waiting for approval', approved: 'Approved', denied: 'Denied', expired: 'Expired',
  executing: 'In progress', succeeded: 'Done', failed: 'Failed', uncertain: 'Outcome unknown', cancelled: 'Cancelled',
}
const runStates: Record<string, string> = {
  queued: 'Queued', running: 'Coordinator working…', waiting_for_approval: 'Waiting for your approval', cancelling: 'Stopping…',
  completed: 'Completed', failed: 'Failed', cancelled: 'Stopped', interrupted: 'Interrupted',
}

/** Roadmap proposal: plain text only (React escapes every value; no HTML is interpreted). */
function RoadmapPlan({ plan }: { plan: NonNullable<ReturnType<typeof roadmapPreview>> }) {
  return <div className="agent-plan">
    {plan.objective && <p className="agent-objective"><strong>Objective:</strong> {plan.objective}</p>}
    {plan.rationale && <p className="agent-purpose">{plan.rationale}</p>}
    {plan.tasks.length > 0 && <ol className="agent-plan-tasks" aria-label="Proposed tasks">{plan.tasks.map((task, index) => <li key={index}>
      <span className="agent-task-title">{task.title}</span>
      {task.status && task.status !== 'pending' && <small> · {task.status.replace('_', ' ')}</small>}
      {task.depends_on.length > 0 && <small className="agent-task-after"> after {task.depends_on.join(', ')}</small>}
      {task.acceptance_criteria.length > 0 && <ul className="agent-criteria">{task.acceptance_criteria.map((criterion, i) => <li key={i}>{criterion}</li>)}</ul>}
    </li>)}</ol>}
    {plan.removed.length > 0 && <p role="note" className="agent-warning">Removes {plan.removed.length === 1 ? 'task' : 'tasks'}: {plan.removed.join(', ')}</p>}
    {plan.revision !== null && <p className="agent-binding">Bound to roadmap revision {plan.revision}</p>}
  </div>
}

export function ApprovalCard({ approval, onDecided, guard }: { approval: ApprovalView; onDecided?: () => void; guard?: EditGuard }) {
  const title = approval.preview.title || 'Proposed action'
  const plan = roadmapPreview(approval.preview)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [unknown, setUnknown] = useState(() => !!pendingAgent(scopes.decision(approval.id)))
  const send = async (decision: 'approve' | 'deny') => {
    setBusy(true); setError('')
    try { await decide(approval, decision); setUnknown(false); onDecided?.() }
    catch (e) { setUnknown(unknownOutcome(e)); setError(String(e instanceof Error ? e.message : e)) }
    finally { setBusy(false) }
  }
  const stored = pendingAgent(scopes.decision(approval.id))?.body.decision as 'approve' | 'deny' | undefined
  const pending = approval.state === 'pending'
  const blocked = pending ? approveBlocked(approval, guard) : null
  const reasonId = `approval-reason-${approval.id}`
  const before = textOf(approval.preview.before), source = textOf(approval.preview.source), explanation = textOf(approval.preview.explanation)
  const warning = textOf(approval.preview.warning), instructions = textOf(approval.preview.instructions)
  const criteria = !plan && Array.isArray(approval.preview.acceptance_criteria) ? approval.preview.acceptance_criteria.filter((c): c is string => typeof c === 'string') : []
  return <section className="agent-approval" role="region" aria-label={`Approval: ${title}`}>
    <h3>{title}</h3>
    {approval.preview.purpose && <p className="agent-purpose">{approval.preview.purpose}</p>}
    {!!approval.preview.actions?.length && <ul className="agent-actions">{approval.preview.actions.map(action => <li key={action}>{action}</li>)}</ul>}
    {plan && <RoadmapPlan plan={plan} />}
    {explanation && <p className="agent-purpose">{explanation}</p>}
    {instructions && <p className="agent-purpose"><strong>Instructions:</strong> {instructions}</p>}
    {criteria.length > 0 && <ul className="agent-criteria" aria-label="Acceptance criteria">{criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>}
    {before && <><p className="agent-code-label">Before</p><pre className="agent-code">{before}</pre></>}
    {source && <><p className="agent-code-label">{before ? 'After' : 'Source'}</p><pre className="agent-code">{source}</pre></>}
    {warning && <p role="note" className="agent-warning">{warning}</p>}
    {pending ? <>
      <p className="agent-expiry">Expires <time dateTime={new Date(approval.expires_at * 1000).toISOString()}>{new Date(approval.expires_at * 1000).toLocaleString(undefined, { timeStyle: 'short', dateStyle: 'short' })}</time></p>
      {unknown && stored ? <p role="status" className="agent-warning">Your decision ({stored}) has an unknown outcome. <button type="button" disabled={busy} onClick={() => void send(stored)}>Retry</button></p>
        : <div className="agent-buttons"><button type="button" className="agent-approve" disabled={busy || !!blocked} aria-describedby={blocked ? reasonId : undefined} onClick={() => void send('approve')}>Approve</button><button type="button" disabled={busy} onClick={() => void send('deny')}>Deny</button>{blocked && <span id={reasonId} className="agent-blocked">{blocked}</span>}</div>}
    </> : <p className="agent-decided">{({ pending: '', approved: 'Approved', denied: 'Denied', expired: 'Expired before a decision', invalidated: 'No longer valid' } as const)[approval.state]}</p>}
    {error && <p role="alert" className="agent-error">{error}</p>}
  </section>
}

type PanelProps = { run: RunView; onChanged?: () => void; onRevealCell?: (cellId: string) => void; children?: RunView[]; guard?: EditGuard }

/** A run's progress panel. Child runs render as siblings after the parent's <details>, so a
 * collapsed completed parent never hides an active child's approval card. */
export function AgentRunPanel({ run, onChanged, onRevealCell, children = [], guard }: PanelProps) {
  return <>
    <RunDetails run={run} onChanged={onChanged} onRevealCell={onRevealCell} guard={guard} />
    {children.map(child => <Fragment key={child.id}><RunDetails run={child} onChanged={onChanged} onRevealCell={onRevealCell} guard={guard} /></Fragment>)}
  </>
}

function RunDetails({ run, onChanged, onRevealCell, guard }: Omit<PanelProps, 'children'>) {
  const active = !terminalRunStates.includes(run.state) && run.state !== 'waiting_for_approval'
  const awaiting = run.approvals.some(approval => approval.state === 'pending')
  const [open, setOpen] = useState(active || awaiting)
  const [stopping, setStopping] = useState(false), [error, setError] = useState('')
  useEffect(() => { setOpen(active || awaiting) }, [active, awaiting])
  const done = terminalRunStates.includes(run.state)
  const actions = run.tool_calls.length
  const summary = run.state === 'completed' ? `Completed · ${actions} action${actions === 1 ? '' : 's'}` : runStates[run.state] ?? run.state
  const stop = async () => {
    setStopping(true); setError('')
    try { await cancelRun(run.id); onChanged?.() } catch (e) { setError(`Stopping has an unknown outcome: ${e instanceof Error ? e.message : String(e)}`) }
    finally { setStopping(false) }
  }
  return <details className={`agent-run state-${run.state}`} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{summary}</summary>
    {!!run.tool_calls.length && <ul className="agent-calls">{run.tool_calls.map(call => <li key={call.id} data-state={call.state}>
      <span>{toolLabels[call.name] ?? call.name}</span> <small>{callStates[call.state] ?? call.state}</small>
      {call.summary && call.state !== 'awaiting_approval' && <p>{call.summary}</p>}
      {onRevealCell && typeof call.links?.cell_id === 'string' && <button type="button" className="agent-reveal" onClick={() => onRevealCell(call.links!.cell_id as string)}>Show cell</button>}
    </li>)}</ul>}
    {run.approvals.filter(approval => approval.state === 'pending').map(approval => <ApprovalCard key={approval.id} approval={approval} onDecided={onChanged} guard={guard} />)}
    {run.state === 'failed' && run.error && <p role="alert" className="agent-error">{run.error}</p>}
    {run.state === 'interrupted' && <p className="agent-warning">The server restarted while an action was in progress and its outcome could not be verified, so it was not retried. Check the workspace before asking again.</p>}
    {!done && run.state !== 'cancelling' && <div className="agent-stop"><button type="button" disabled={stopping} onClick={() => void stop()}>Stop agent</button><small>Stopping ends future agent work only; it does not undo applied changes.</small></div>}
    {error && <p role="alert" className="agent-error">{error}</p>}
  </details>
}
