export type AgentEventType =
  | 'run.started'
  | 'run.resumed'
  | 'run.waiting_for_input'
  | 'input.submitted'
  | 'input.accepted'
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

export type RunStatus = 'idle' | 'pending' | 'running' | 'recovering' | 'waiting_for_input' | 'completed' | 'failed' | 'cancelled'

export interface HumanInputRequest {
  requestId: string
  questions: string[]
  allowUseAvailable?: boolean
}

export type RenderHint = 'chart' | 'table' | 'tree' | 'cards'

export interface ToolRun {
  id: string
  name: string
  status: 'running' | 'completed' | 'failed'
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
  toolResults?: ToolRun[]
  runId?: string
  inputRequest?: HumanInputRequest
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  updatedAt: string
}


export type AgentTaskStatusGroup = 'pending' | 'running' | 'ended'

export interface AgentTaskSummary {
  id: string
  site_id: string
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
