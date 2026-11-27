import { isSpecialist, readSpecialistProfile, specialistStatusLabels, type SpecialistStatus } from '../specialists'
import type { AgentEvent, AgentEventType, AppliedSkill, RenderHint, RunState, SpecialistRun, ToolRun } from '../types/agent'
import type { AgentRunDetail } from '../services/agentApi'

export const initialRunState: RunState = {
  status: 'idle',
  stage: '等待输入',
  assistantText: '',
  agentRuns: [],
  skills: [],
  tools: [],
  events: [],
  lastSequence: 0,
  lastDeltaSequence: 0,
}

const renderHints = new Set<RenderHint>(['chart', 'table', 'tree', 'cards'])

const stageLabels: Record<string, string> = {
  calling_model: '正在分析问题',
  summarizing_budget: '正在基于现有证据总结',
}

const isSpecialistRun = (value: unknown): value is SpecialistRun =>
  !!value && typeof value === 'object'
    && 'id' in value && typeof value.id === 'string' && 'agent' in value && isSpecialist(value.agent)
    && 'startedAt' in value && typeof value.startedAt === 'string'
    && 'status' in value && typeof value.status === 'string' && Object.hasOwn(specialistStatusLabels, value.status)

const nonEmptyText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined

const isAppliedSkill = (value: unknown): value is AppliedSkill =>
  value !== null && typeof value === 'object' && 'agent' in value && 'name' in value && 'label' in value
    && isSpecialist(value.agent) && typeof value.name === 'string' && typeof value.label === 'string'

const parseRenderHint = (value: unknown): RenderHint | undefined =>
  typeof value === 'string' && renderHints.has(value as RenderHint) ? value as RenderHint : undefined

const upsertTool = (tools: ToolRun[], next: ToolRun): ToolRun[] => {
  if (!tools.some(tool => tool.id === next.id)) return [...tools, next]
  return tools.map(tool => tool.id === next.id ? { ...tool, ...next } : tool)
}

const specialistEventStatuses: Partial<Record<AgentEventType, SpecialistStatus>> = {
  'agent.dispatched': 'running',
  'agent.started': 'running',
  'agent.completed': 'completed',
  'agent.failed': 'failed',
  'run.waiting_for_input': 'waiting_for_input',
}

function applySpecialistEvent(runs: SpecialistRun[], event: AgentEvent): SpecialistRun[] {
  const { agent, agent_run_id: runId, agent_profile: payloadProfile } = event.payload
  const id = nonEmptyText(runId)
  if (!id || (agent !== undefined && !isSpecialist(agent))) return runs
  const status = Object.hasOwn(specialistEventStatuses, event.type) ? specialistEventStatuses[event.type] : undefined
  const profile = readSpecialistProfile(payloadProfile)
  if (!status && !profile) return runs

  const run = runs.find(item => item.id === id)
  if (!run) {
    if (status !== 'running' || !isSpecialist(agent)) return runs
    return [...runs, { id, agent, ...(profile && { profile }), status, startedAt: event.created_at }]
  }
  if (agent !== undefined && run.agent !== agent) return runs
  return runs.map(item => item.id === id ? {
    ...item,
    status: status ?? item.status,
    ...(profile && { profile }),
  } : item)
}

const stopOpenSpecialists = (runs: SpecialistRun[]): SpecialistRun[] =>
  runs.map(run => run.status === 'running' || run.status === 'waiting_for_input'
    ? { ...run, status: 'cancelled' }
    : run)

const stopOpenTools = (tools: ToolRun[]): ToolRun[] =>
  tools.map(tool => tool.status === 'running' ? { ...tool, status: 'cancelled' } : tool)

export function readRunSnapshot(detail: AgentRunDetail, afterSequence = 0, prior?: RunState, throughSequence = Infinity): RunState {
  let state = prior ?? initialRunState
  for (const item of detail.events ?? []) {
    const type = item.event_type ?? item.type
    if (!type || item.sequence <= afterSequence || item.sequence > throughSequence) continue
    state = reduceAgentEvent(state, {
      event_id: item.event_id ?? item.id ?? String(item.sequence), run_id: detail.id, type,
      sequence: item.sequence, created_at: item.created_at, payload: item.payload,
    })
  }
  const status = detail.status.toLowerCase()
  if (['pending', 'running', 'recovering', 'recovery_blocked', 'waiting_for_input', 'completed', 'failed', 'cancelled'].includes(status)) {
    return { ...state, runId: detail.id, status: status as RunState['status'], error: detail.error_message ?? state.error }
  }
  return state
}

export function reduceAgentEvent(state: RunState, event: AgentEvent): RunState {
  if (event.type === 'run.reset') {
    return {
      ...initialRunState,
      status: String(event.payload.status ?? 'idle') as RunState['status'],
      stage: event.payload.status === 'pending' ? '正在准备任务' : initialRunState.stage,
      agentRuns: Array.isArray(event.payload.priorAgentRuns) ? event.payload.priorAgentRuns.filter(isSpecialistRun) : [],
      skills: Array.isArray(event.payload.priorSkills) ? event.payload.priorSkills.filter(isAppliedSkill) : [],
    }
  }
  const deltaEvent = event.type === 'message.delta'
  const stableEvent = event.type !== 'heartbeat' && !deltaEvent
  if (deltaEvent && event.sequence <= state.lastDeltaSequence) return state
  if (stableEvent && event.sequence <= state.lastSequence) return state
  const base: RunState = {
    ...state,
    runId: event.run_id,
    agentRuns: applySpecialistEvent(state.agentRuns, event),
    events: stableEvent ? [...state.events, event] : state.events,
    lastSequence: stableEvent ? event.sequence : state.lastSequence,
    lastDeltaSequence: event.type === 'run.resumed'
      ? event.sequence
      : deltaEvent
        ? event.sequence
        : state.lastDeltaSequence,
  }
  switch (event.type) {
    case 'run.started':
      return { ...base, status: 'running', stage: '正在分析问题', error: undefined }
    case 'run.resumed':
      return { ...base, status: 'recovering', stage: '正在恢复任务', humanInput: undefined, error: undefined }
    case 'run.waiting_for_input':
      return {
        ...base, status: 'waiting_for_input', stage: '等待你补充信息',
        humanInput: {
          requestId: String(event.payload.request_id),
          questions: Array.isArray(event.payload.questions) ? event.payload.questions.map(String) : [],
          allowUseAvailable: event.payload.allow_use_available === true,
        },
      }
    case 'stage.changed': {
      const stage = String(event.payload.stage ?? '')
      return { ...base, stage: stageLabels[stage] ?? '处理中' }
    }
    case 'skill.loaded': {
      const skill = {
        agent: event.payload.agent,
        agentRunId: nonEmptyText(event.payload.agent_run_id),
        name: event.payload.skill,
        label: event.payload.label,
      }
      if (!isAppliedSkill(skill)
        || state.skills.some(item => item.agent === skill.agent && item.name === skill.name && item.agentRunId === skill.agentRunId)) return base
      return { ...base, skills: [...state.skills, skill] }
    }
    case 'message.delta':
      return { ...base, assistantText: state.assistantText + String(event.payload.delta ?? ''), stage: '正在生成回答' }
    case 'message.completed':
      return { ...base, assistantText: String(event.payload.content ?? state.assistantText) }
    case 'tool.started':
    case 'tool.completed':
    case 'tool.failed': {
      const agentRunId = nonEmptyText(event.payload.agent_run_id)
      const agent = isSpecialist(event.payload.agent) ? event.payload.agent : undefined
      const callId = String(event.payload.tool_call_id ?? event.event_id)
      const id = agentRunId ? `${agentRunId}:${callId}` : callId
      const tool = { id, name: String(event.payload.name ?? 'tool'), agent, agentRunId }
      if (event.type === 'tool.started') {
        return {
          ...base, stage: '正在读取数据',
          tools: upsertTool(state.tools, { ...tool, status: 'running', arguments: event.payload.arguments }),
        }
      }
      const result = event.payload.result
      const resultError = result && typeof result === 'object' && 'error' in result
        ? nonEmptyText(result.error)
        : undefined
      return {
        ...base,
        stage: event.type === 'tool.completed' ? '工具执行完成' : '工具执行失败',
        tools: upsertTool(state.tools, {
          ...tool,
          status: event.type === 'tool.completed' ? 'completed' : 'failed',
          result,
          render_hint: parseRenderHint(event.payload.render_hint),
          error: event.type === 'tool.failed' ? nonEmptyText(event.payload.error) ?? resultError : undefined,
          latencyMs: typeof event.payload.latency_ms === 'number' ? event.payload.latency_ms : undefined,
        }),
      }
    }
    case 'run.completed': {
      const partial = event.payload.partial === true
      return {
        ...base,
        status: 'completed',
        error: undefined,
        humanInput: undefined,
        stage: partial ? '已基于现有证据完成' : '已完成',
        partial,
        completionMode: String(event.payload.completion_mode ?? 'normal'),
      }
    }
    case 'run.failed':
      return {
        ...base, status: 'failed', humanInput: undefined, stage: '执行失败',
        agentRuns: stopOpenSpecialists(base.agentRuns),
        tools: stopOpenTools(state.tools),
        error: String(event.payload.message ?? 'Agent 执行失败'),
      }
    case 'run.cancelled':
      return {
        ...base, status: 'cancelled', humanInput: undefined, stage: '已停止',
        agentRuns: stopOpenSpecialists(base.agentRuns),
        tools: stopOpenTools(state.tools),
      }
    default:
      return base
  }
}
