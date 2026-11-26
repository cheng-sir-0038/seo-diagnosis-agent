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
    window.history.replaceState(null, '', '/')
    vi.unstubAllGlobals()
  })

  it('loads the observability page and requests traces', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<ObservabilityPage />)
    expect(await screen.findByRole('heading', { name: '运行中心 / 观测面板' })).toBeInTheDocument()
    expect(await screen.findByText('暂无运行记录')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces'), expect.anything()))
  })

  it('uses one window for the overview, traces and metrics, with independent headline counts', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input))
      if (url.pathname.endsWith('/overview')) {
        return json(url.searchParams.has('start_at') ? {
          trace_count: 132, failed_trace_count: 4, waiting_trace_count: 0, tool_call_count: 18,
          previous: { trace_count: 125, failed_trace_count: 6, tool_call_count: 16 },
        } : { trace_count: 230, failed_trace_count: 10, waiting_trace_count: 2, tool_call_count: 60, previous: null })
      }
      if (url.pathname.endsWith('/traces')) return json([{
        id: 'trace-latest', operation_name: 'workspace.chat', status: 'COMPLETED', started_at: new Date().toISOString(),
      }])
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<ObservabilityPage />)
    const cards = within(screen.getByRole('region', { name: '运行指标' }))
    expect(await cards.findByText('132')).toBeVisible()
    expect(cards.getByText('较前 24 小时 +7')).toBeVisible()
    expect(cards.getByText('较前 24 小时 −2')).toBeVisible()
    expect(within(cards.getByRole('article', { name: '等待补充 Trace' })).getByText('0')).toBeVisible()
    expect(cards.getByText('记录业务执行及计划任务结果')).toBeVisible()
    expect(screen.getByText('1 条已加载记录')).toBeVisible()
    expect(screen.queryByText(/显示最新/)).not.toBeInTheDocument()

    const requests = fetchMock.mock.calls.map(([input]) => new URL(String(input)))
    const windowedRequests = requests.filter(url => url.searchParams.has('start_at'))
    expect(windowedRequests.map(url => url.pathname.split('/').at(-1)).sort()).toEqual(['issues', 'metrics', 'overview', 'traces'])
    const params = windowedRequests[0].searchParams
    const start = params.get('start_at')!
    const end = params.get('end_at')!
    expect(Date.parse(end) - Date.parse(start)).toBe(24 * 3_600_000)
    expect(windowedRequests.every(url => url.searchParams.get('start_at') === start && url.searchParams.get('end_at') === end)).toBe(true)
    const traceRequest = requests.find(url => url.pathname.endsWith('/traces'))!
    expect(traceRequest.searchParams.get('scope')).toBe('product')
    const issueRequest = requests.find(url => url.pathname.endsWith('/issues'))!
    expect([...issueRequest.searchParams.keys()].sort()).toEqual(['end_at', 'hide_maintenance', 'start_at'])
    expect(windowedRequests.filter(url => !url.pathname.endsWith('/traces'))
      .every(url => url.searchParams.get('hide_maintenance') === 'true')).toBe(true)

    fireEvent.mouseDown(screen.getByRole('combobox', { name: '观测时间范围' }))
    fireEvent.click(await screen.findByText('全部时间'))
    expect(await cards.findByText('230')).toBeVisible()
    expect(cards.queryByText('较前 24 小时 +7')).not.toBeInTheDocument()
  })

  it('distinguishes zero failed runs from a recorded tool error', async () => {
    const issue = {
      id: 'issue-read', component: 'seo_page_metadata', issue_type: 'ClientConnectorError',
      title: 'seo_page_metadata: Cannot connect to host site.example', severity: 'ERROR',
      status: 'RESOLVED', last_seen_at: '2026-11-24T00:00:00Z', occurrence_count: 2,
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname
      if (path.endsWith('/overview')) return json({
        trace_count: 5, failed_trace_count: 0, waiting_trace_count: 0, tool_call_count: 2,
        previous: { trace_count: 5, failed_trace_count: 0, tool_call_count: 2 },
      })
      return json(path.endsWith('/issues') ? [issue] : [])
    }))
    render(<ObservabilityPage />)
    const failed = within(screen.getByRole('article', { name: '整次运行失败' }))
    expect(await failed.findByText('0')).toBeVisible()
    expect(failed.getByText('仅统计失败的 Trace，工具报错见「问题」')).toBeVisible()
    expect(failed.queryByText(/持平/)).not.toBeInTheDocument()

    const recent = within(screen.getByRole('region', { name: '最近问题' }))
    expect(recent.getByText('读取网页内容工具失败')).toBeVisible()
    fireEvent.click(recent.getByRole('button', { name: '查看问题列表' }))
    const issues = within(await screen.findByRole('tabpanel', { name: /问题$/ }))
    const row = within(issues.getByRole('list', { name: '已记录的问题' }))
    expect(row.getByRole('heading', { name: '读取网页内容工具失败' })).toBeVisible()
    expect(row.getByText('错误')).toBeVisible()
    expect(row.getByText('最近出现')).toBeVisible()
    expect(row.getByText('累计次数')).toBeVisible()
    expect(issues.queryByRole('combobox')).not.toBeInTheDocument()
    expect(issues.queryByText(/处理状态|待处理问题|已解决/)).not.toBeInTheDocument()
    const disclosure = row.getByText('技术详情').closest('details')!
    expect(disclosure).not.toHaveAttribute('open')
    expect(within(disclosure).getByText(issue.title)).not.toBeVisible()
  })

  it('uses readable metric labels and preserves zero and missing values', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => json(String(input).includes('/observability/metrics') ? [{
      metric_name: 'trace.duration_ms', unit: 'ms', point_count: 7,
      average_value: 2000.828, minimum_value: 0, maximum_value: null,
    }, {
      metric_name: 'issue.occurrence.count', unit: 'occurrences', point_count: 2,
      average_value: 1, minimum_value: 1, maximum_value: 1,
    }] : [])))
    render(<ObservabilityPage />)
    fireEvent.click(screen.getByRole('tab', { name: '指标' }))
    const metrics = within(await screen.findByRole('tabpanel', { name: /指标$/ }))
    expect(metrics.getByRole('columnheader', { name: '样本数' })).toBeVisible()
    const row = within(await metrics.findByRole('row', { name: /Trace 总耗时/ }))
    expect(row.getByText('trace.duration_ms')).toBeVisible()
    expect(row.getByRole('cell', { name: '2,000.83' })).toBeVisible()
    expect(row.getByRole('cell', { name: '0' })).toBeVisible()
    expect(row.getByRole('cell', { name: '—' })).toBeVisible()
    const issueMetric = within(metrics.getByRole('row', { name: /问题发生次数/ }))
    expect(issueMetric.getByText('issue.occurrence.count')).toBeVisible()
    expect(issueMetric.getByRole('cell', { name: '次' })).toBeVisible()
  })

  it('requests the curated Trace scope for both recent runs and exact ID lookup', async () => {
    const base = { status: 'COMPLETED', started_at: '2026-11-24T00:00:00Z' }
    const business = { ...base, id: 'trace-business', operation_name: 'workspace.chat' }
    const scan = { ...base, id: 'trace-scan', operation_name: 'scheduler.opportunity_scan' }
    const outbox = { ...base, id: 'trace-outbox', operation_name: 'scheduler.notification_outbox' }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input))
      if (!url.pathname.endsWith('/traces')) return json([])
      if (url.searchParams.get('scope') !== 'product') throw new Error('Unscoped Trace request')
      const identifier = url.searchParams.get('identifier')
      if (identifier) return json(identifier === scan.id ? [scan] : [outbox])
      return json([outbox, business])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<ObservabilityPage />)
    const tracePanel = within(screen.getByRole('tabpanel', { name: 'Trace' }))
    expect(await tracePanel.findByRole('button', { name: '主 Agent 对话' })).toBeVisible()
    expect(tracePanel.queryByText(business.id)).not.toBeInTheDocument()
    expect(tracePanel.queryByText('系统任务')).not.toBeInTheDocument()
    expect(tracePanel.getAllByRole('row')).toHaveLength(2)
    const search = tracePanel.getByRole('textbox', { name: '搜索 Trace' })
    const lookup = (value: string) => {
      fireEvent.change(search, { target: { value } })
      fireEvent.click(tracePanel.getByRole('button', { name: '按 ID 查找' }))
    }
    lookup(' trace-scan ')
    expect(await tracePanel.findByRole('button', { name: /定时扫描 SEO 机会.*trace-scan/ })).toBeVisible()
    lookup('trace-outbox')
    expect(await tracePanel.findByText('没有匹配此 ID 的 Trace')).toBeVisible()
    fireEvent.click(tracePanel.getByRole('button', { name: '返回列表' }))
    expect(tracePanel.getByRole('button', { name: '主 Agent 对话' })).toBeVisible()
  })

  it('shows the real status and tool span, then links a Trace to its saved conversation', async () => {
    const trace = {
      id: 'trace_waiting', operation_name: 'workspace.chat', status: 'INTERRUPTED',
      started_at: '2026-11-24T00:00:00Z',
      metadata_json: JSON.stringify({ application_run_id: 'run_waiting', application_status: 'waiting_for_input' }),
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (new URL(url).pathname.endsWith('/observability/traces/trace_waiting')) return json({
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
            { role: 'system', content: [
              '确认出行日期和会场。',
              '仅输出符合以下 JSON Schema 的 JSON 对象，不要输出解释或 Markdown：',
              '{"type":"object","properties":{"venue":{"type":"string"}}}',
            ].join('\n') },
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
      return json(new URL(url).pathname.endsWith('/observability/traces') ? [trace] : [])
    })
    vi.stubGlobal('fetch', fetchMock)
    const onOpen = vi.fn()
    render(<ObservabilityPage conversationForRun={id => id === 'run_waiting' ? { id: 'local', title: '本地会话' } : undefined} onOpenConversation={onOpen} />)
    expect(await screen.findByText('等待补充')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /本地会话/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces/trace_waiting'), expect.anything()))
    const child = await screen.findByText('220 ms')
    expect(child.closest('li')?.parentElement?.closest('li')).toHaveTextContent('商旅行程专员')
    const detail = within(child.closest('.obs-detail-body')!)
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
    const systemPrompt = prompts.getByRole('region', { name: '系统提示词正文' })
    expect(systemPrompt).toHaveTextContent('确认出行日期和会场。')
    expect(systemPrompt).not.toHaveTextContent('venue')
    expect(prompts.queryByTestId('raw-tool-result')).not.toBeInTheDocument()
    fireEvent.click(prompts.getByText('JSON Schema', { exact: true }))
    const formattedSchema = await prompts.findByRole('region', { name: '格式化内容' })
    expect(formattedSchema).toHaveTextContent('"venue": {')
    expect(within(formattedSchema).getByRole('button', { name: '复制内容' })).toBeVisible()
    expect(prompts.getByText(/我要去武汉出差。\s+周五回来。/)).toBeVisible()
    expect(prompts.queryByText(/confirmed_request|current_date/)).not.toBeInTheDocument()
    expect(prompts.getByText('暂无已记录的提示词正文。')).toBeVisible()
    fireEvent.click(detail.getByRole('tab', { name: '事件日志' }))
    const events = within(detail.getByRole('tabpanel', { name: '事件日志' }))
    expect(events.getByText('开始调用工具 · 查目的地天气和出行建议')).toBeVisible()
    expect(events.getByText('生成回答 · 2 段')).toBeVisible()
    expect(events.getByText('步骤执行失败 · 生成页面 SEO 建议')).toBeVisible()
    expect(events.getByText('已复用工具结果 · 拟定页面建议')).toBeVisible()
    expect(events.queryByText('事件数据')).not.toBeInTheDocument()
    expect(events.queryByText('message.delta')).not.toBeInTheDocument()
    const eventSummary = events.getByText('生成回答 · 2 段').closest('summary')!
    fireEvent.click(eventSummary)
    expect(await events.findByText('message.delta')).toBeVisible()
    expect(eventSummary.closest('details')).toHaveAttribute('open')
    fireEvent.click(detail.getByRole('button', { name: '返回关联会话' }))
    expect(onOpen).toHaveBeenCalledWith('local', 'run_waiting')
  }, 12_000)

  it('reopens a business detail from its address even outside the loaded list', async () => {
    window.history.replaceState(null, '', '/#/observability?trace=trace-old')
    const requested = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input))
      if (url.pathname.endsWith('/trace-old')) return json({
        id: 'trace-old', operation_name: 'scheduler.weekly_digest', status: 'COMPLETED', started_at: '2026-10-01T00:00:00Z',
      })
      return json([])
    })
    vi.stubGlobal('fetch', requested)
    render(<ObservabilityPage />)
    expect(await screen.findByText('生成 SEO 周报')).toBeVisible()
    expect(requested).toHaveBeenCalledWith(expect.stringContaining('/trace-old?hide_maintenance=true'), expect.anything())
    fireEvent.click(screen.getByRole('button', { name: '返回列表' }))
    expect(window.location.hash).toBe('#/observability')
    expect(screen.getByRole('tab', { name: 'Trace' })).toHaveAttribute('aria-selected', 'true')
  })

  it('never renders technical details for a legacy maintenance response', async () => {
    window.history.replaceState(null, '', '/#/observability?trace=trace-hidden')
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => json(String(input).includes('/trace-hidden') ? {
      id: 'trace-hidden', operation_name: 'scheduler.automation_sync', status: 'COMPLETED',
      error_message: 'Maintenance details',
    } : [])))
    render(<ObservabilityPage />)
    expect(await screen.findByText('这条运行记录不存在或不在运行中心的展示范围内。')).toBeVisible()
    expect(screen.queryByText('Maintenance details')).not.toBeInTheDocument()
  })

  it('explains the zero-tool document flow using the actual model span', async () => {
    const trace = { id: 'trace-documents', operation_name: 'workspace.documents', status: 'COMPLETED', started_at: '2026-11-24T00:00:00Z' }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (new URL(url).pathname.endsWith('/traces/trace-documents')) return json({
        ...trace,
        runs: [{ id: 'run-document', agent_name: 'workspace-supervisor', spans: [
          { id: 'model-span', kind: 'MODEL', operation_name: 'model.iteration.1', status: 'COMPLETED' },
        ] }],
        tool_calls: [],
        model_calls: [{ id: 'model', model: 'deepseek-v4-flash', operation: 'stream', status: 'COMPLETED' }],
      })
      return json(new URL(url).pathname.endsWith('/traces') ? [trace] : [])
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
