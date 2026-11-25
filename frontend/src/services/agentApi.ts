import { workspaceHeaders } from '../state/workspaceAccess'
import type {
  AgentEvent, AgentTaskStatusGroup, AgentTaskSummary, ChatMessage, SpecialistDirectoryData, WorkspaceTaskRow,
} from '../types/agent'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const CHAT_RUNS = '/api/v1/chat/runs'
const TERMINAL_EVENTS = new Set<AgentEvent['type']>(['run.completed', 'run.failed', 'run.cancelled', 'run.waiting_for_input'])

export function agentApiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export async function listWorkspaceTools(conversationId: string, signal?: AbortSignal): Promise<SpecialistDirectoryData> {
  const query = new URLSearchParams({ conversation_id: conversationId })
  const response = await fetch(agentApiUrl(`/api/v1/chat/tools?${query}`), { signal })
  if (!response.ok) throw new Error(`读取工具目录失败（${response.status}）`)
  return response.json()
}

export async function listWorkspaceTasks(conversationIds: string[], signal?: AbortSignal): Promise<WorkspaceTaskRow[]> {
  const query = new URLSearchParams()
  for (const id of conversationIds) query.append('conversation_id', id)
  const response = await fetch(agentApiUrl(`/api/v1/chat/tasks?${query}`), { signal })
  if (!response.ok) throw new Error(`读取任务失败（${response.status}）`)
  return response.json()
}

export interface CreateRunResponse {
  run_id: string
  task_id?: string
  thread_id?: string
  status: string
  events_url: string
  cancel_url: string
  after_sequence?: number
  followup_key?: string | null
}

export async function submitAgentInput(
  runId: string, requestId: string, answer: string,
  action: 'answer' | 'use_available' = 'answer', followupKey?: string,
): Promise<CreateRunResponse> {
  const headers = new Headers({ 'Content-Type': 'application/json', ...workspaceHeaders() })
  if (followupKey) headers.set('X-Followup-Key', followupKey)
  const response = await fetch(agentApiUrl(`${CHAT_RUNS}/${runId}/input`), {
    method: 'POST', headers,
    body: JSON.stringify({ request_id: requestId, answer, action }),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    const message = typeof detail.detail === 'string' ? detail.detail
      : response.status === 422 ? '补充信息格式不正确，请输入 1 至 2000 字的内容。'
        : `提交补充信息失败（${response.status}）`
    throw Object.assign(new Error(message), { status: response.status })
  }
  return response.json() as Promise<CreateRunResponse>
}

export interface AgentRunDetail {
  id: string
  status: string
  current_stage?: string | null
  task_status?: string | null
  error_message?: string | null
  checkpoint?: { id: string } | null
  events?: Array<{
    id?: string; event_id?: string; event_type?: AgentEvent['type']; type?: AgentEvent['type']
    sequence: number; created_at: string; payload: AgentEvent['payload']
  }>
}

export interface WorkspaceRunStats {
  elapsed_ms: number | null
  total_tokens: number | null
}

export async function getWorkspaceRunStats(runId: string, signal?: AbortSignal): Promise<WorkspaceRunStats> {
  const response = await fetch(agentApiUrl(`${CHAT_RUNS}/${encodeURIComponent(runId)}/stats`), {
    headers: workspaceHeaders(),
    signal,
  })
  if (!response.ok) throw new Error('无法读取运行统计')
  return response.json()
}

export interface AgentStreamOptions {
  signal?: AbortSignal
  afterEventId?: number
  maxReconnectAttempts?: number
  retryBaseMs?: number
  onEventId?: (eventId: number) => void
  onReconnect?: (attempt: number, cause: Error) => void
}

export interface AgentStreamResult {
  lastEventId: number
  terminal: boolean
}

export class AgentStreamConnectionError extends Error {
  readonly lastEventId: number
  readonly recoverable: boolean

  constructor(
    message: string,
    options: { lastEventId: number; recoverable?: boolean },
  ) {
    super(message)
    this.name = 'AgentStreamConnectionError'
    this.lastEventId = options.lastEventId
    this.recoverable = options.recoverable ?? true
  }
}

export async function createAgentRun(messages: ChatMessage[], conversationId?: string): Promise<CreateRunResponse> {
  const documentIds = messages.at(-1)?.documents?.map(({ id }) => id)
  let includeTurn = true
  const context = messages.flatMap(({ role, content, documents }, index) => {
    if (role === 'user') includeTurn = !documents?.length || index === messages.length - 1
    return includeTurn ? [{ role, content }] : []
  })
  const response = await fetch(agentApiUrl(CHAT_RUNS), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(documentIds?.length ? workspaceHeaders() : {}),
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      messages: context,
      ...(documentIds?.length ? { document_ids: documentIds } : {}),
    }),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(typeof detail.detail === 'string' ? detail.detail
      : detail.detail?.message ?? `创建 Agent 运行失败（${response.status}）`)
  }
  return response.json() as Promise<CreateRunResponse>
}

export async function streamAgentEvents(
  eventsUrl: string,
  onEvent: (event: AgentEvent) => void,
  signalOrOptions?: AbortSignal | AgentStreamOptions,
): Promise<AgentStreamResult> {
  const options = normalizeStreamOptions(signalOrOptions)
  const maxReconnectAttempts = options.maxReconnectAttempts ?? 5
  const retryBaseMs = options.retryBaseMs ?? 800
  let lastEventId = Math.max(0, options.afterEventId ?? 0)
  let reconnectAttempt = 0

  while (true) {
    try {
      const result = await openEventStream(eventsUrl, onEvent, {
        ...options,
        afterEventId: lastEventId,
        onEventId: (value) => {
          lastEventId = Math.max(lastEventId, value)
          options.onEventId?.(lastEventId)
        },
      })
      return { ...result, lastEventId }
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause))
      if (error.name === 'AbortError') throw error
      const recoverable = !(error instanceof AgentStreamConnectionError) || error.recoverable
      if (!recoverable || reconnectAttempt >= maxReconnectAttempts) {
        throw new AgentStreamConnectionError(
          error.message || '事件流连接已中断',
          { lastEventId, recoverable },
        )
      }
      reconnectAttempt += 1
      options.onReconnect?.(reconnectAttempt, error)
      await waitForRetry(retryBaseMs * Math.min(2 ** (reconnectAttempt - 1), 6), options.signal)
    }
  }
}

async function openEventStream(
  eventsUrl: string,
  onEvent: (event: AgentEvent) => void,
  options: AgentStreamOptions,
): Promise<AgentStreamResult> {
  const url = withCursor(eventsUrl, options.afterEventId ?? 0)
  const headers: Record<string, string> = { Accept: 'text/event-stream', ...workspaceHeaders() }
  if ((options.afterEventId ?? 0) > 0) headers['Last-Event-ID'] = String(options.afterEventId)
  const response = await fetch(`${API_BASE}${url}`, { signal: options.signal, headers })
  if (!response.ok || !response.body) {
    throw new AgentStreamConnectionError(
      `连接事件流失败（${response.status}）`,
      { lastEventId: options.afterEventId ?? 0, recoverable: response.status >= 500 || response.status === 429 },
    )
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  let lastEventId = options.afterEventId ?? 0
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const parsed = parseSseFrame(frame)
      if (parsed.eventId !== undefined) {
        lastEventId = Math.max(lastEventId, parsed.eventId)
        options.onEventId?.(lastEventId)
      }
      if (!parsed.data || parsed.data === '{}') continue
      const event = JSON.parse(parsed.data) as AgentEvent
      onEvent(event)
      if (TERMINAL_EVENTS.has(event.type)) terminal = true
    }
  }
  if (!terminal) {
    throw new AgentStreamConnectionError(
      '事件流连接提前结束',
      { lastEventId, recoverable: true },
    )
  }
  return { lastEventId, terminal }
}

function parseSseFrame(frame: string): { eventId?: number; data: string } {
  let eventId: number | undefined
  const data: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('id:')) {
      const value = Number.parseInt(line.slice(3).trim(), 10)
      if (Number.isFinite(value)) eventId = value
    } else if (line.startsWith('data:')) {
      data.push(line.slice(5).replace(/^ /, ''))
    }
  }
  return { eventId, data: data.join('\n') }
}

function withCursor(eventsUrl: string, cursor: number): string {
  if (cursor <= 0) return eventsUrl
  const separator = eventsUrl.includes('?') ? '&' : '?'
  return `${eventsUrl}${separator}after_sequence=${cursor}`
}

function normalizeStreamOptions(value?: AbortSignal | AgentStreamOptions): AgentStreamOptions {
  if (!value) return {}
  if ('aborted' in value && typeof value.addEventListener === 'function') {
    return { signal: value as AbortSignal }
  }
  return value as AgentStreamOptions
}

function waitForRetry(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (delayMs <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, delayMs)
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

export async function getAgentRun(runId: string, signal?: AbortSignal): Promise<AgentRunDetail> {
  const headers = workspaceHeaders()
  const url = agentApiUrl(`${CHAT_RUNS}/${runId}`)
  const response = signal || headers['X-Workspace-Key'] ? await fetch(url, { headers, signal }) : await fetch(url)
  if (!response.ok) throw new Error(`读取 Agent 运行失败（${response.status}）`)
  return response.json() as Promise<AgentRunDetail>
}

export async function resumeAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
  const response = await fetch(agentApiUrl(`${CHAT_RUNS}/${runId}/resume`), { method: 'POST', ...workspaceRequestHeaders() })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(typeof error.detail === 'string' ? error.detail : `恢复任务失败（${response.status}）`)
  }
  return response.json() as Promise<{ run_id: string; status: string }>
}

export async function cancelAgentRun(cancelUrl: string): Promise<{ run_id: string; status: string }> {
  const response = await fetch(`${API_BASE}${cancelUrl}`, { method: 'POST', ...workspaceRequestHeaders() })
  if (!response.ok) throw new Error(`停止运行失败（${response.status}）`)
  return response.json()
}

function workspaceRequestHeaders(): Pick<RequestInit, 'headers'> {
  const headers = workspaceHeaders()
  return headers['X-Workspace-Key'] ? { headers } : {}
}

export async function listAgentTasks(
  siteId: string,
  statusGroup?: AgentTaskStatusGroup,
  limit = 20,
): Promise<AgentTaskSummary[]> {
  const params = new URLSearchParams({ site_id: siteId, limit: String(limit) })
  if (statusGroup) params.set('status_group', statusGroup)
  const response = await fetch(`${API_BASE}/api/v1/seo-agent/chat/tasks?${params.toString()}`)
  if (!response.ok) throw new Error(`加载 Agent 任务失败（${response.status}）`)
  return response.json() as Promise<AgentTaskSummary[]>
}
