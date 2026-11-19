import { json, request } from './api'
import type { Message, RecordData } from './applicationApi'

export type AgentScope = { kind: 'workspace' | 'project' | 'notebook'; resource_id?: string; directory?: string }
export type AgentConfig = { enabled: boolean; model: string; provider: string; test_provider: boolean; supports_tools: boolean; error?: string }
export type AgentState = 'queued' | 'running' | 'waiting_for_approval' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'interrupted' | 'needs_reconciliation'
export type AgentApproval = { id: string; digest: string; tool: string; target: Record<string, unknown>; arguments: Record<string, unknown>; expires_at: number }
export type AgentRun = { id: string; role: string; state: AgentState; approval: AgentApproval | null; parent_id: string | null; error: string | null; messages: {message_id:string;purpose:string}[] }
export type AgentEvent = { sequence: number; kind: string; data: Record<string, unknown> }
export type Handoff = { id: string; run_id: string; notebook_id: string; project_id: string; path: string; source_conversation_id: string; destination_conversation_id: string; plan: {objective:string} }
export const liveRun = (state: AgentState) => ['queued','running','waiting_for_approval','cancelling'].includes(state)
export const stateLabel: Record<AgentState,string> = {queued:'Queued',running:'Working',waiting_for_approval:'Waiting for approval',cancelling:'Stopping future steps',completed:'Completed',failed:'Failed',cancelled:'Cancelled',interrupted:'Interrupted',needs_reconciliation:'Outcome needs review'}
export const agentConfig = (signal?: AbortSignal) => request<AgentConfig>('/agents/configuration',{signal})
export const agentConversation = (scope: AgentScope, signal?:AbortSignal) => request<{id:string}>(`/agents/conversation?${new URLSearchParams(Object.entries(scope))}`,{signal})
export const agentRuns = (conversation: string,signal?:AbortSignal) => request<{items:AgentRun[];next_before:number|null}>(`/agents/runs?conversation_id=${conversation}&limit=12`,{signal})
export const agentMessages = (conversation:string,before=0,signal?:AbortSignal) => request<{items:RecordData<Message>[];next_before:number|null}>(`/agents/conversations/${conversation}/messages?before=${before}`,{signal})
export const agentHandoffs = (conversation:string,signal?:AbortSignal) => request<{items:Handoff[]}>(`/agents/conversations/${conversation}/handoffs`,{signal})
export const agentEvents = (run:string,after:number,signal?:AbortSignal) => request<{items:AgentEvent[];next_after:number;has_more:boolean}>(`/agents/runs/${run}/events?after=${after}`,{signal})
export const agentStart = (body: object) => request<{id:string;conversation_id:string;message_id:string}>('/agents/runs',json('POST',body))
export const agentDecision = (run:string,approval:AgentApproval,decision:'granted'|'denied',operation_id:string) => request(`/agents/runs/${run}/approvals/${approval.id}`,json('POST',{digest:approval.digest,decision,operation_id}))
export const agentControl = (run:string,action:'cancel'|'resume'|'reconcile',operation_id:string) => request(`/agents/runs/${run}/${action}`,json('POST',{operation_id}))
