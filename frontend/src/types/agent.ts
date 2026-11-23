export type AgentEventType =
  | 'run.started'
  | 'run.resumed'
  | 'run.waiting_for_input'
  | 'input.submitted'
  | 'input.accepted'
  | 'agent.dispatched'
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

export type RunStatus = 'idle' | 'pending' | 'running' | 'recovering' | 'waiting_for_input' | 'completed' | 'failed' | 'cancelled'

export interface HumanInputRequest {
  requestId: string
  questions: string[]
  allowUseAvailable?: boolean
}

export type RenderHint = 'chart' | 'table' | 'tree' | 'cards'

export const specialistNames = { seo: 'SEO / GEO', healthcare: '医药业务' } as const
export type Specialist = keyof typeof specialistNames

export interface AppliedSkill {
  agent: Specialist
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
  agents: Specialist[]
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
  toolResults?: ToolRun[]
  agents?: Specialist[]
  skills?: AppliedSkill[]
  runId?: string
  inputRequest?: HumanInputRequest
}

export interface SavedAgentRun {
  priorAgents?: Specialist[]
  priorSkills?: AppliedSkill[]
  runId: string
  eventsUrl: string
  cancelUrl: string
  afterSequence: number
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  updatedAt: string
  activeRun?: SavedAgentRun
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
