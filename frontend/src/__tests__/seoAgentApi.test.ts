import { afterEach, describe, expect, it, vi } from 'vitest'

import { createAgentRun, listAgentTasks } from '../services/agentApi'
import type { AgentEvent, ChatMessage } from '../types/agent'

const message: ChatMessage = {
  id: 'm1',
  role: 'user',
  content: '分析自然流量下降',
  createdAt: '2026-07-24T00:00:00Z',
}

describe('SEO Agent API', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('creates a site-bound SEO Agent run', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(() => Promise.resolve(new Response(JSON.stringify({
      run_id: 'run_1',
      task_id: 'agtask_1',
      thread_id: 'chat_1',
      status: 'pending',
      events_url: '/api/v1/seo-agent/chat/runs/run_1/events',
      cancel_url: '/api/v1/seo-agent/chat/runs/run_1/cancel',
    }), { status: 201, headers: { 'Content-Type': 'application/json' } })))
    vi.stubGlobal('fetch', fetchMock)

    await createAgentRun([message], { siteId: 'site_1', conversationId: 'conversation_1' })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/seo-agent/chat/runs', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toMatchObject({ site_id: 'site_1', conversation_id: 'conversation_1' })
  })

  it('loads persisted Agent tasks by site and status group', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(() => Promise.resolve(new Response(JSON.stringify([{
      id: 'agtask_1',
      site_id: 'site_1',
      thread_id: 'chat_1',
      title: '检查 TDK',
      user_intent: '检查 TDK',
      status: 'COMPLETED',
      status_group: 'ended',
      created_at: '2026-07-27T00:00:00Z',
      updated_at: '2026-07-27T00:01:00Z',
    }]), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    vi.stubGlobal('fetch', fetchMock)

    const tasks = await listAgentTasks('site_1', 'ended', 12)

    expect(tasks[0].id).toBe('agtask_1')
    expect(String(fetchMock.mock.calls[0][0])).toContain('site_id=site_1')
    expect(String(fetchMock.mock.calls[0][0])).toContain('status_group=ended')
    expect(String(fetchMock.mock.calls[0][0])).toContain('limit=12')
  })

})

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

it('reconnects an interrupted SSE stream from the last stable event id', async () => {
  const { streamAgentEvents } = await import('../services/agentApi')
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(sseResponse([
      'id: 1\nevent: run.started\ndata: {"event_id":"evt_1","run_id":"run_1","type":"run.started","sequence":1,"created_at":"2026-07-27T00:00:00Z","payload":{"status":"running"}}\n\n',
      'id: 2\nevent: tool.completed\ndata: {"event_id":"evt_2","run_id":"run_1","type":"tool.completed","sequence":2,"created_at":"2026-07-27T00:00:01Z","payload":{"tool_call_id":"tool_1","name":"seo_page_metadata"}}\n\n',
    ]))
    .mockResolvedValueOnce(sseResponse([
      'id: 5\nevent: message.completed\ndata: {"event_id":"evt_5","run_id":"run_1","type":"message.completed","sequence":5,"created_at":"2026-07-27T00:00:05Z","payload":{"content":"完整答案"}}\n\n',
      'id: 6\nevent: run.completed\ndata: {"event_id":"evt_6","run_id":"run_1","type":"run.completed","sequence":6,"created_at":"2026-07-27T00:00:06Z","payload":{"status":"completed"}}\n\n',
    ]))
  vi.stubGlobal('fetch', fetchMock)
  const events: AgentEvent[] = []
  const cursors: number[] = []

  const result = await streamAgentEvents('/events', (item) => events.push(item), {
    maxReconnectAttempts: 1,
    retryBaseMs: 0,
    onEventId: (value) => cursors.push(value),
  })

  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(String(fetchMock.mock.calls[1][0])).toContain('after_sequence=2')
  expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>)['Last-Event-ID']).toBe('2')
  expect(events.map((item) => item.type)).toEqual([
    'run.started', 'tool.completed', 'message.completed', 'run.completed',
  ])
  expect(cursors).toEqual([1, 2, 5, 6])
  expect(result).toEqual({ lastEventId: 6, terminal: true })
})

it('reads and resumes an existing Agent run', async () => {
  const { getAgentRun, resumeAgentRun } = await import('../services/agentApi')
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      id: 'run_1', status: 'FAILED', current_stage: 'failed', error_message: 'upstream error',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      run_id: 'run_1', status: 'recovering',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)

  const detail = await getAgentRun('run_1')
  const resumed = await resumeAgentRun('run_1')

  expect(detail.status).toBe('FAILED')
  expect(resumed.status).toBe('recovering')
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/seo-agent/chat/runs/run_1')
  expect(fetchMock.mock.calls[1]).toEqual([
    '/api/v1/seo-agent/chat/runs/run_1/resume',
    { method: 'POST' },
  ])
})
