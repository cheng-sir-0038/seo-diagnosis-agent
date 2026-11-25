import type { Specialist, SpecialistStatus } from '../specialists'
export type { Specialist } from '../specialists'

export type AgentEventType =
  | 'run.started'
  | 'run.resumed'
  | 'run.waiting_for_input'
  | 'input.submitted'
  | 'input.accepted'
  | 'agent.dispatched'
  | 'agent.completed'
  | 'agent.failed'
  | 'skill.loaded'
  | 'stage.changed'
  | 'message.started'
  | 'message.delta'
  | 'message.completed'
  | 'tool.started'
  | 'tool.completed'
  | 'tool.failed'
  | 'run.completed'
  | 'run.failed'
  | 'run.cancelled'
  | 'heartbeat'
  | 'run.reset'

export interface AgentEvent {
  event_id: string
  run_id: string
  type: AgentEventType
  sequence: number
  created_at: string
  payload: Record<string, unknown>
}

export type RunStatus = 'idle' | 'pending' | 'running' | 'recovering' | 'recovery_blocked' | 'waiting_for_input' | 'completed' | 'failed' | 'cancelled'

export interface HumanInputRequest {
  requestId: string
  questions: string[]
  allowUseAvailable?: boolean
}

export type RenderHint = 'chart' | 'table' | 'tree' | 'cards'

export interface SpecialistRun {
  id: string
  agent: Specialist
  status: SpecialistStatus
  startedAt: string
}

export interface AppliedSkill {
  agent: Specialist
  agentRunId?: string
  name: string
  label: string
}

export interface SpecialistDirectoryData {
  specialists: {
    agent: Specialist
    tools: { name: string, description: string }[]
    skills?: { name: string, label: string, tools: string[] }[]
    note?: string | null
  }[]
}

export interface ToolRun {
  id: string
  name: string
  agent?: Specialist
  agentRunId?: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  arguments?: unknown
  result?: unknown
  error?: string
  render_hint?: RenderHint
  latencyMs?: number
}

export interface RunState {
  runId?: string
  humanInput?: HumanInputRequest
  status: RunStatus
  stage: string
  assistantText: string
  agentRuns: SpecialistRun[]
  skills: AppliedSkill[]
  tools: ToolRun[]
  events: AgentEvent[]
  lastSequence: number
  lastDeltaSequence: number
  partial?: boolean
  completionMode?: string
  error?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  documents?: DocumentSummary[]
  toolResults?: ToolRun[]
  agentRuns?: SpecialistRun[]
  skills?: AppliedSkill[]
  runId?: string
  runStatus?: RunStatus
  error?: string
  afterSequence?: number
  throughSequence?: number
  partial?: boolean
  inputRequest?: HumanInputRequest
  followupKey?: string
}

export interface SavedAgentRun {
  replyId?: string
  priorAgentRuns?: SpecialistRun[]
  priorSkills?: AppliedSkill[]
  runId: string
  eventsUrl: string
  cancelUrl: string
  afterSequence: number
  followupKey?: string
}

export interface PendingInputSubmission {
  runId: string
  requestId: string
  messageId: string
  action: 'answer' | 'use_available'
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  updatedAt: string
  activeRun?: SavedAgentRun
  pendingInputSubmission?: PendingInputSubmission
  draftDocuments?: DocumentSummary[]
}

export interface WorkspaceTaskRow {
  id: string
  conversation_id: string
  current_run_id: string | null
  title: string
  status: string
  can_resume: boolean
  partial: boolean
  created_at: string
  updated_at: string
}

export type AgentTaskStatusGroup = 'pending' | 'running' | 'ended'

export interface AgentTaskSummary {
  id: string
  site_id: string | null
  conversation_id?: string | null
  thread_id: string
  title: string
  user_intent: string
  status: string
  status_group: AgentTaskStatusGroup
  current_stage?: string | null
  current_run_id?: string | null
  survival_summary?: Record<string, unknown>
  error_message?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
}
import type { DocumentSummary } from './workspace'
