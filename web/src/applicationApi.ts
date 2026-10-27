import { ApiError, json, request } from './api'

export interface RecordData<T> { id: string; revision: number; notebook_id: string | null; data: T }
export interface Links { notebook_id?: string | null; cell_id?: string | null; task_id?: string | null; message_id?: string | null; document_revision?: number | null; execution_receipt_id?: string | null }
export type TaskStatus = 'pending' | 'in_progress' | 'blocked' | 'needs_input' | 'completed' | 'cancelled'
export interface Task { id: string; title: string; acceptance_criteria: string[]; parent_id: string | null; dependencies: string[]; status: TaskStatus; explanation: string | null; weight: number; links: Links; evidence: { summary: string; message_id?: string | null; execution_receipt_id?: string | null }[]; accepted: boolean }
export interface TaskView extends Task { progress: number; display: { compact: boolean; checkmark: boolean; show_progress: boolean }; accepted_by?: string | null }
export interface Roadmap { objective: string; tasks: TaskView[]; progress: number }
export interface Project { title: string; status: string; notebooks: { notebook_id: string; present: boolean }[] }
export interface Conversation { title: string }
export interface Message extends Links { content: string; parent_id: string | null; state: string; author_role: string }
export interface Comment { project_id: string; cell_id: string; content: string; parent_id: string | null; resolved: boolean; thread_id: string; anchor_status: string; classification: string }
export interface Activity extends Links { summary: string; result: string }
export async function all<T>(path: string): Promise<RecordData<T>[]> {
  let cursor: number | null = 0
  const records: RecordData<T>[] = []
  do {
    const page: { items: RecordData<T>[]; next_cursor: number | null } = await request(`${path}${path.includes('?') ? '&' : '?'}after=${cursor}&limit=100`)
    records.push(...page.items); cursor = page.next_cursor
  } while (cursor !== null)
  return records
}
export function rawTask(task: TaskView | Task): Task {
  const { id, title, acceptance_criteria, parent_id, dependencies, status, explanation, weight, links, evidence, accepted } = task
  return { id, title, acceptance_criteria, parent_id, dependencies, status, explanation, weight, links, evidence, accepted }
}

type Pending = { path: string; method: string; body: object }
const key = 'europa.pending.application'
export function pendingMutation(): Pending | null {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') } catch { return null }
}
export async function retryMutation<T>(): Promise<T> {
  const pending = pendingMutation()
  if (!pending) throw new Error('No pending operation')
  try {
    const result = await request<T>(pending.path, json(pending.method, pending.body))
    localStorage.removeItem(key); return result
  } catch (e) {
    if (e instanceof ApiError && e.status < 500) localStorage.removeItem(key)
    throw e
  }
}
export async function change<T>(path: string, method: string, body: object): Promise<T> {
  if (pendingMutation()) throw new Error('An earlier operation has an unknown outcome. Retry that exact operation before making another change.')
  // Persist before sending so a lost response / page refresh reuses the same ID.
  localStorage.setItem(key, JSON.stringify({ path, method, body: { ...body, operation_id: crypto.randomUUID() } }))
  return retryMutation<T>()
}
