import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ObservabilityPage } from '../pages/ObservabilityPage'
import { PromptOpsPage } from '../pages/PromptOpsPage'

const json = (data: unknown) =>
  Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))

function mockProductFetch() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/observability/traces')) return json([])
    if (url.includes('/observability/issues')) return json([])
    if (url.includes('/observability/metrics')) return json([])
    if (url.includes('/promptops/prompts')) return json([])
    if (url.includes('/promptops/datasets')) return json([])
    if (url.includes('/promptops/evaluations')) return json([])
    if (url.includes('/promptops/experiments')) return json([])
    return json([])
  })
}

describe('Observability and PromptOps product pages', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('loads the observability page and requests traces', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<ObservabilityPage />)
    expect(await screen.findByRole('heading', { name: '运行中心 / 观测面板' })).toBeInTheDocument()
    expect(await screen.findByText('暂无 Trace')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces'), expect.anything()))
  })

  it('uses readable metric labels and preserves zero and missing values', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => json(String(input).includes('/observability/metrics') ? [{
      metric_name: 'trace.duration_ms', unit: 'ms', point_count: 7,
      average_value: 2000.828, minimum_value: 0, maximum_value: null,
    }] : [])))
    render(<ObservabilityPage />)
    fireEvent.click(screen.getByRole('tab', { name: '指标' }))
    const metrics = within(screen.getByRole('tabpanel', { name: '指标' }))
    expect(metrics.getByRole('columnheader', { name: '样本数' })).toBeVisible()
    const row = within(await metrics.findByRole('row', { name: /Trace 总耗时/ }))
    expect(row.getByText('trace.duration_ms')).toBeVisible()
    expect(row.getByRole('cell', { name: '2,000.83' })).toBeVisible()
    expect(row.getByRole('cell', { name: '0' })).toBeVisible()
    expect(row.getByRole('cell', { name: '—' })).toBeVisible()
  })

  it('shows the real status and tool span, then links a Trace to its saved conversation', async () => {
    const trace = {
      id: 'trace_waiting', operation_name: 'workspace.chat', status: 'INTERRUPTED',
      started_at: '2026-11-24T00:00:00Z',
      metadata_json: JSON.stringify({ application_run_id: 'run_waiting', application_status: 'waiting_for_input' }),
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/observability/traces/trace_waiting')) return json({
        ...trace,
        runs: [{ id: 'trace-run', trigger_type: 'api', agent_name: 'workspace-supervisor', status: 'INTERRUPTED', spans: [
          { id: 'parent', kind: 'AGENT', operation_name: 'specialist:travel', status: 'COMPLETED' },
          { id: 'child', parent_span_id: 'parent', kind: 'TOOL', operation_name: 'travel_weather', status: 'COMPLETED', duration_ms: 220 },
          { id: 'draft-model', kind: 'MODEL', operation_name: 'seo.page.suggestions', status: 'FAILED' },
          { id: 'reused-draft', kind: 'TOOL', operation_name: 'seo_page_suggestions.reused', status: 'COMPLETED' },
        ] }],
        tool_calls: [{
          id: 'tool-weather', span_id: 'child', tool_call_id: 'call-weather', tool_name: 'travel_weather',
          status: 'COMPLETED', latency_ms: 220, retry_count: 0, started_at: trace.started_at,
          arguments_json: JSON.stringify({ city: '武汉' }),
          result_summary_json: JSON.stringify({ source: 'Open-Meteo' }),
        }],
        model_calls: [{
          id: 'model-1', model: 'deepseek-v4-flash', operation: 'workspace.travel.intake',
          prompt_key: 'workspace.travel.intake', prompt_version: 'v2', status: 'COMPLETED',
          latency_ms: 520, total_tokens: 1200,
          input_summary_json: JSON.stringify({ business_object: { run_id: 'run_waiting' }, messages: [
            { role: 'system', content: '确认出行日期和会场。' },
            { type: 'human', content: JSON.stringify({
              current_date: '2026-11-24',
              confirmed_request: { origin: '上海' },
              user_message: '我要去武汉出差。\n周五回来。',
            }) },
          ] }),
        }, {
          id: 'model-2', model: 'deepseek-v4-flash', operation: 'workspace.route', status: 'COMPLETED',
          prompt_key: 'workspace.route.v7', prompt_version: 'v1',
          input_summary_json: JSON.stringify({ message_count: 2 }),
        }],
        events: [
          { id: 'event-1', span_id: 'child', event_type: 'tool.started', occurred_at: trace.started_at,
            sequence: 1, payload_json: '{"name":"travel_weather"}' },
          { id: 'event-2', event_type: 'message.delta', occurred_at: trace.started_at,
            sequence: 2, payload_json: '{"text":"武"}' },
          { id: 'event-3', event_type: 'message.delta', occurred_at: trace.started_at,
            sequence: 3, payload_json: '{"text":"汉"}' },
          { id: 'event-4', span_id: 'draft-model', event_type: 'span.failed', occurred_at: trace.started_at,
            sequence: 4, payload_json: '{}' },
          { id: 'event-5', event_type: 'tool.completed', occurred_at: trace.started_at,
            sequence: 5, payload_json: '{"name":"seo_page_suggestions","reused":true}' },
        ],
      })
      return json(url.endsWith('/observability/traces') ? [trace] : [])
    })
    vi.stubGlobal('fetch', fetchMock)
    const onOpen = vi.fn()
    render(<ObservabilityPage conversationForRun={id => id === 'run_waiting' ? { id: 'local', title: '本地会话' } : undefined} onOpenConversation={onOpen} />)
    expect(await screen.findByText('等待补充')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /本地会话/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces/trace_waiting'), expect.anything()))
    const child = await screen.findByText('220 ms')
    expect(child.closest('li')?.parentElement?.closest('li')).toHaveTextContent('商旅行程专员')
    const detail = within(child.closest('.observability-detail-body')!)
    const flow = within(detail.getByRole('tabpanel', { name: '运行链路' }))
    expect(flow.getByText('生成页面 SEO 建议')).toBeVisible()
    expect(flow.getByText('复用页面建议结果')).toBeVisible()
    expect(flow.queryByText(/seo\.page\.suggestions|seo_page_suggestions\.reused/)).not.toBeInTheDocument()
    const inlineTool = within(flow.getByText('travel_weather').closest('details')!)
    fireEvent.click(inlineTool.getByText('查目的地天气和出行建议'))
    expect(inlineTool.getByText('call-weather')).toBeVisible()
    fireEvent.click(inlineTool.getByText('调用参数'))
    const inlineJson = await inlineTool.findByRole('region', { name: '格式化内容' })
    expect(inlineJson).toHaveTextContent(/"city": "武汉"/)
    expect(within(inlineJson).getByRole('button', { name: '复制内容' })).toBeVisible()
    fireEvent.click(detail.getByRole('tab', { name: '工具调用' }))
    const tools = within(detail.getByRole('tabpanel', { name: '工具调用' }))
    expect(tools.getByText('travel_weather')).not.toBeVisible()
    expect(tools.getByText('220 ms')).toBeVisible()
    fireEvent.click(tools.getByText('查目的地天气和出行建议'))
    expect(tools.getByText('travel_weather')).toBeVisible()
    fireEvent.click(tools.getByText('调用参数'))
    expect(await tools.findByRole('region', { name: '格式化内容' })).toHaveTextContent(/"city": "武汉"/)
    fireEvent.click(detail.getByRole('tab', { name: '提示词' }))
    const prompts = within(detail.getByRole('tabpanel', { name: '提示词' }))
    const routing = within(prompts.getByText('理解需求并分派专员').closest('li')!)
    expect(routing.getByText('workspace.route.v7')).not.toBeVisible()
    fireEvent.click(routing.getByText('技术信息'))
    expect(routing.getByText('workspace.route.v7')).toBeVisible()
    expect(prompts.getByText('版本 v2')).toBeVisible()
    expect(prompts.getByText('1,200 tokens')).toBeVisible()
    fireEvent.click(prompts.getByText('系统提示词'))
    fireEvent.click(prompts.getByText('用户输入'))
    expect(prompts.getByText('确认出行日期和会场。')).toBeVisible()
    expect(prompts.getByText(/我要去武汉出差。\s+周五回来。/)).toBeVisible()
    expect(prompts.queryByText(/confirmed_request|current_date/)).not.toBeInTheDocument()
    expect(prompts.getByText('暂无已记录的提示词正文。')).toBeVisible()
    fireEvent.click(detail.getByRole('tab', { name: '事件日志' }))
    const events = within(detail.getByRole('tabpanel', { name: '事件日志' }))
    expect(events.getByText('开始调用工具 · 查目的地天气和出行建议')).toBeVisible()
    expect(events.getByText('生成回答 · 2 段')).toBeVisible()
    expect(events.getByText('步骤执行失败 · 生成页面 SEO 建议')).toBeVisible()
    expect(events.getByText('已复用工具结果 · 拟定页面建议')).toBeVisible()
    expect(events.getByText('message.delta')).not.toBeVisible()
    fireEvent.click(detail.getByRole('button', { name: '返回关联会话' }))
    expect(onOpen).toHaveBeenCalledWith('local', 'run_waiting')
  }, 12_000)

  it('explains the zero-tool document flow using the actual model span', async () => {
    const trace = { id: 'trace-documents', operation_name: 'workspace.documents', status: 'COMPLETED', started_at: '2026-11-24T00:00:00Z' }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/traces/trace-documents')) return json({
        ...trace,
        runs: [{ id: 'run-document', agent_name: 'workspace-supervisor', spans: [
          { id: 'model-span', kind: 'MODEL', operation_name: 'model.iteration.1', status: 'COMPLETED' },
        ] }],
        tool_calls: [],
        model_calls: [{ id: 'model', model: 'deepseek-v4-flash', operation: 'stream', status: 'COMPLETED' }],
      })
      return json(url.endsWith('/traces') ? [trace] : [])
    }))
    render(<ObservabilityPage />)
    fireEvent.click(await screen.findByRole('button', { name: /资料问答/ }))
    expect(await screen.findByText('由模型直接生成资料答复，本次工具调用 0 次。')).toBeVisible()
    expect(screen.getByText('生成资料答复')).toBeVisible()
    expect(screen.getByText('主 Agent')).toBeVisible()
    expect(screen.queryByText('model.iteration.1')).not.toBeInTheDocument()
  })

  it('loads the PromptOps page and shows the empty state', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<PromptOpsPage />)
    expect(await screen.findByRole('heading', { name: '提示词实验' })).toBeInTheDocument()
    expect(await screen.findByText('暂无 Prompt')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/promptops/prompts'), expect.anything()))
  })
})
