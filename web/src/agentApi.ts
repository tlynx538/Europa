import { ApiError, json, request } from './api'
import type { Message, RecordData } from './applicationApi'

export type RunState = 'queued' | 'running' | 'waiting_for_approval' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export interface AgentStatus { enabled: boolean; provider: string | null; model: string | null; reason: string | null }
export interface ApprovalPreview { title?: string; actions?: string[]; purpose?: string; [key: string]: unknown }
/** Roadmap preview fields from propose_roadmap. Values arrive as untrusted JSON; render them only through roadmapPreview(). */
export interface RoadmapPreviewTask { title: string; depends_on: string[]; acceptance_criteria: string[]; status: string | null }
export interface RoadmapPreview { objective: string | null; rationale: string | null; tasks: RoadmapPreviewTask[]; removed: string[]; revision: number | null }
const text = (value: unknown) => typeof value === 'string' && value.trim() ? value : null
const texts = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
/** Validated roadmap preview, or null when the preview is not a roadmap. Unknown fields are ignored. */
export function roadmapPreview(preview: ApprovalPreview): RoadmapPreview | null {
  if (!Array.isArray(preview.tasks)) return null
  const tasks = preview.tasks.flatMap(task => {
    if (!task || typeof task !== 'object') return []
    const item = task as Record<string, unknown>
    const title = text(item.title)
    return title ? [{ title, depends_on: texts(item.depends_on), acceptance_criteria: texts(item.acceptance_criteria), status: text(item.status) }] : []
  })
  const revision = typeof preview.roadmap_revision === 'number' && Number.isInteger(preview.roadmap_revision) ? preview.roadmap_revision : null
  return { objective: text(preview.objective), rationale: text(preview.rationale), tasks, removed: texts(preview.removed_task_ids), revision }
}
export interface ApprovalView { id: string; tool: string; state: 'pending' | 'approved' | 'denied' | 'expired' | 'invalidated'; digest: string; preview: ApprovalPreview; expires_at: number; created_at?: number; decided_at?: number | null; reason?: string | null }
export interface ToolCallView { id: string; name: string; state: string; summary: string | null; status: string | null; links: Record<string, string | number | null> | null; data: { notebook_id?: string; notebook_path?: string; project_id?: string; [key: string]: unknown } | null }
export interface RunView {
  id: string; role: string; state: RunState; conversation_id: string; trigger_message_id: string | null; parent_run_id?: string | null
  notebook_id: string | null; project_id: string | null; provider: string; model: string; error: string | null
  result: { message_id?: string; links?: Record<string, string> } | null; created_at: number; updated_at: number; finished_at: number | null
  approvals: ApprovalView[]; tool_calls: ToolCallView[]
  depth?: number; task?: { kickoff?: boolean; roadmap_id?: string; task_id?: string; title?: string; assigned_by?: 'user' | 'coordinator' | null } | null
}
export interface Decision { id: string; run_id: string; state: string; decided_at: number | null }

export const activeRunStates: RunState[] = ['queued', 'running', 'cancelling']
export const terminalRunStates: RunState[] = ['completed', 'failed', 'cancelled', 'interrupted']
export const demoLabel = 'Deterministic demo provider (not an LLM)'
export const providerLabel = (status: AgentStatus) => status.provider === 'demo' ? demoLabel : [status.provider, status.model].filter(Boolean).join(' · ')

export const agentStatus = () => request<AgentStatus>('/agent/status')
export const homeConversation = () => request<RecordData<{ title: string }>>('/agent/home/conversation')
export const conversationRuns = async (conversation: string) =>
  (await request<{ items: RunView[] }>(`/agent/runs?conversation_id=${encodeURIComponent(conversation)}`)).items

// ---------------------------------------------------------------- persist-before-send
// Like applicationApi.change(): the request (with its operation_id) is stored before it
// is sent, cleared only on success or a definitive 4xx, and every retry reuses it.
type Pending = { path: string; body: Record<string, unknown> & { operation_id: string } }
export const pendingKey = (scope: string) => `europa.agent.pending.${scope}`
export const scopes = {
  message: (conversation: string) => `message.${conversation}`,
  run: (conversation: string) => `run.${conversation}`,
  decision: (approval: string) => `approval.${approval}`,
  cancel: (run: string) => `cancel.${run}`,
  assign: (roadmap: string, task: string) => `assign.${roadmap}.${task}`,
  home: 'home.new',
}

export function pendingAgent(scope: string): Pending | null {
  try { return JSON.parse(localStorage.getItem(pendingKey(scope)) ?? 'null') } catch { return null }
}

export async function retryAgent<T>(scope: string): Promise<T> {
  const pending = pendingAgent(scope)
  if (!pending) throw new Error('No pending agent operation')
  try {
    const result = await request<T>(pending.path, json('POST', pending.body))
    localStorage.removeItem(pendingKey(scope)); return result
  } catch (e) {
    if (e instanceof ApiError && e.status < 500) localStorage.removeItem(pendingKey(scope))
    throw e
  }
}

const same = (pending: Pending, path: string, body: Record<string, unknown>) => {
  const { operation_id: _, ...stored } = pending.body
  return pending.path === path && JSON.stringify(stored) === JSON.stringify(body)
}

/** Send `body` once. An identical earlier request with an unknown outcome is re-sent with its
 * original operation_id; a different one must be resolved first. */
export async function persistAndSend<T>(scope: string, path: string, body: Record<string, unknown>): Promise<T> {
  const pending = pendingAgent(scope)
  if (pending && !same(pending, path, body)) throw new Error('An earlier Coordinator request has an unknown outcome. Retry it before sending another.')
  if (!pending) localStorage.setItem(pendingKey(scope), JSON.stringify({ path, body: { ...body, operation_id: crypto.randomUUID() } }))
  return retryAgent<T>(scope)
}

export const postHomeMessage = (conversation: string, content: string) =>
  persistAndSend<RecordData<Message>>(scopes.message(conversation), `/conversations/${encodeURIComponent(conversation)}/messages`, { content })
export const startRun = (conversation: string, message: string) =>
  persistAndSend<RunView>(scopes.run(conversation), '/agent/runs', { conversation_id: conversation, message_id: message })
export const decide = (approval: ApprovalView, decision: 'approve' | 'deny' | 'repropose') =>
  persistAndSend<Decision>(scopes.decision(approval.id), `/agent/approvals/${encodeURIComponent(approval.id)}`, { decision, digest: approval.digest })
export const cancelRun = (run: string) =>
  persistAndSend<RunView>(scopes.cancel(run), `/agent/runs/${encodeURIComponent(run)}/cancel`, {})
/** Owner assignment of one roadmap task to the Coding Agent (Phase 4D). */
export const assignTask = (roadmap: string, task: string) =>
  persistAndSend<RunView>(scopes.assign(roadmap, task), '/agent/tasks/assign', { roadmap_id: roadmap, task_id: task })
export const newHomeConversation = () =>
  persistAndSend<RecordData<{ title: string }>>(scopes.home, '/agent/home/conversations', {})

/** True when a failure leaves the outcome unknown (stored request kept for an exact retry). */
export const unknownOutcome = (error: unknown) => !(error instanceof ApiError && error.status < 500)
