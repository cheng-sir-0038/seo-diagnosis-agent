import type { AgentEvent, RenderHint, RunState, ToolRun } from '../types/agent'

export const initialRunState: RunState = {
  status: 'idle',
  stage: '等待输入',
  assistantText: '',
  tools: [],
  events: [],
  lastSequence: 0,
  lastDeltaSequence: 0,
}

const renderHints = new Set<RenderHint>(['chart', 'table', 'tree', 'cards'])

const parseRenderHint = (value: unknown): RenderHint | undefined =>
  typeof value === 'string' && renderHints.has(value as RenderHint) ? value as RenderHint : undefined

const upsertTool = (tools: ToolRun[], next: ToolRun): ToolRun[] => {
  const index = tools.findIndex((tool) => tool.id === next.id)
  if (index === -1) return [...tools, next]
  return tools.map((tool, position) => (position === index ? { ...tool, ...next } : tool))
}

export function reduceAgentEvent(state: RunState, event: AgentEvent): RunState {
  if (event.type === 'run.reset') {
    return { ...initialRunState, status: String(event.payload.status ?? 'idle') as RunState['status'] }
  }
  const deltaEvent = event.type === 'message.delta'
  const stableEvent = event.type !== 'heartbeat' && !deltaEvent
  if (deltaEvent && event.sequence <= state.lastDeltaSequence) return state
  if (stableEvent && event.sequence <= state.lastSequence) return state
  const base: RunState = {
    ...state,
    runId: event.run_id,
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
      return { ...base, status: 'running', stage: '正在分析问题' }
    case 'run.resumed':
      return { ...base, status: 'recovering', stage: '正在恢复任务' }
    case 'stage.changed':
      return { ...base, stage: String(event.payload.stage ?? '处理中') }
    case 'message.delta':
      return { ...base, assistantText: state.assistantText + String(event.payload.delta ?? ''), stage: '正在生成回答' }
    case 'message.completed':
      return { ...base, assistantText: String(event.payload.content ?? state.assistantText) }
    case 'tool.started': {
      const id = String(event.payload.tool_call_id ?? event.event_id)
      return {
        ...base,
        stage: '正在调用工具',
        tools: upsertTool(state.tools, { id, name: String(event.payload.name ?? 'tool'), status: 'running', arguments: event.payload.arguments }),
      }
    }
    case 'tool.completed':
    case 'tool.failed': {
      const id = String(event.payload.tool_call_id ?? event.event_id)
      return {
        ...base,
        stage: event.type === 'tool.completed' ? '工具执行完成' : '工具执行失败',
        tools: upsertTool(state.tools, {
          id,
          name: String(event.payload.name ?? 'tool'),
          status: event.type === 'tool.completed' ? 'completed' : 'failed',
          result: event.payload.result,
          render_hint: parseRenderHint(event.payload.render_hint),
          error: event.type === 'tool.failed' ? String(event.payload.error ?? '') : undefined,
          latencyMs: typeof event.payload.latency_ms === 'number' ? event.payload.latency_ms : undefined,
        }),
      }
    }
    case 'run.completed': {
      const partial = event.payload.partial === true
      return {
        ...base,
        status: 'completed',
        stage: partial ? '已基于现有证据完成' : '已完成',
        partial,
        completionMode: String(event.payload.completion_mode ?? 'normal'),
      }
    }
    case 'run.failed':
      return { ...base, status: 'failed', stage: '执行失败', error: String(event.payload.message ?? 'Agent 执行失败') }
    case 'run.cancelled':
      return { ...base, status: 'cancelled', stage: '已停止' }
    default:
      return base
  }
}
