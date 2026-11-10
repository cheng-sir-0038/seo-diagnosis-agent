import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '../pages/ChatPage'
import { saveProductConfig } from '../services/productApi'
import type { AgentEvent } from '../types/agent'

const createAgentRun = vi.fn()
const streamAgentEvents = vi.fn()
const cancelAgentRun = vi.fn()
const listAgentTasks = vi.fn()
const getAgentRun = vi.fn()
const resumeAgentRun = vi.fn()

vi.mock('../services/agentApi', () => ({
  createAgentRun: (...args: unknown[]) => createAgentRun(...args),
  streamAgentEvents: (...args: unknown[]) => streamAgentEvents(...args),
  cancelAgentRun: (...args: unknown[]) => cancelAgentRun(...args),
  listAgentTasks: (...args: unknown[]) => listAgentTasks(...args),
  getAgentRun: (...args: unknown[]) => getAgentRun(...args),
  resumeAgentRun: (...args: unknown[]) => resumeAgentRun(...args),
}))

const event = (sequence: number, type: AgentEvent['type'], payload: Record<string, unknown>): AgentEvent => ({
  event_id: `evt_${sequence}`,
  run_id: 'run_1',
  type,
  sequence,
  created_at: '2026-07-24T00:00:00Z',
  payload,
})

describe('ChatPage SEO Agent integration', () => {
  beforeEach(() => {
    window.localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    saveProductConfig({
      siteId: 'site_1',
      ownerActorId: 'owner_1',
      agentActorId: 'agent_1',
      executorActorId: 'executor_1',
    })
    createAgentRun.mockResolvedValue({
      run_id: 'run_1',
      task_id: 'agtask_12345678',
      thread_id: 'chat_12345678',
      status: 'pending',
      events_url: '/api/v1/seo-agent/chat/runs/run_1/events',
      cancel_url: '/api/v1/seo-agent/chat/runs/run_1/cancel',
    })
    listAgentTasks.mockResolvedValue([
      {
        id: 'agtask_done',
        site_id: 'site_1',
        thread_id: 'chat_done',
        title: '历史 TDK 检查',
        user_intent: '历史 TDK 检查',
        status: 'COMPLETED',
        status_group: 'ended',
        current_stage: 'completed',
        current_run_id: 'run_done',
        created_at: '2026-07-27T00:00:00Z',
        updated_at: '2026-07-27T00:01:00Z',
      },
      {
        id: 'agtask_running',
        site_id: 'site_1',
        thread_id: 'chat_running',
        title: '自然关键词分析',
        user_intent: '自然关键词分析',
        status: 'RUNNING',
        status_group: 'running',
        current_stage: 'calling_model',
        created_at: '2026-07-27T00:02:00Z',
        updated_at: '2026-07-27T00:03:00Z',
      },
      {
        id: 'agtask_pending',
        site_id: 'site_1',
        thread_id: 'chat_pending',
        title: '移动端 PageSpeed',
        user_intent: '移动端 PageSpeed',
        status: 'PENDING',
        status_group: 'pending',
        current_stage: 'created',
        created_at: '2026-07-27T00:04:00Z',
        updated_at: '2026-07-27T00:04:00Z',
      },
    ])
    getAgentRun.mockResolvedValue({ id: 'run_1', status: 'COMPLETED', current_stage: 'completed' })
    resumeAgentRun.mockResolvedValue({ run_id: 'run_1', status: 'recovering' })
    streamAgentEvents.mockImplementation(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', { status: 'running' }))
      onEvent(event(2, 'tool.completed', {
        tool_call_id: 'call_1',
        name: 'seo_organic_keywords',
        render_hint: 'table',
        result: {
          success: true,
          data: {
            schema_version: '1.0',
            capability: 'organic_keywords',
            view_type: 'table',
            title: { key: 'seo.organic_keywords.title', fallback: '关键词明细' },
            description: { key: 'seo.organic_keywords.description', fallback: '查看具体关键词、排名、搜索量和对应页面。' },
            provenance: { source: 'dataforseo', source_label: 'DataForSEO', is_estimated: true, integrity: 'complete' },
            limitations: [],
            columns: [{ key: 'keyword', label: { key: 'seo.field.keyword', fallback: '关键词' }, sortable: true }],
            rows: [{ keyword: 'seo agent' }],
          },
        },
      }))
      onEvent(event(3, 'message.delta', { delta: '分析完成' }))
      onEvent(event(4, 'message.completed', { content: '分析完成' }))
      onEvent(event(5, 'run.completed', { status: 'completed' }))
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('sends the active site and renders structured evidence directly in the chat', async () => {
    render(<ChatPage />)

    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '分析自然流量下降' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await waitFor(() => expect(createAgentRun).toHaveBeenCalled())
    expect(createAgentRun.mock.calls[0][1]).toMatchObject({ siteId: 'site_1' })
    expect(await screen.findByText('Task 12345678 · Thread 12345678')).toBeInTheDocument()

    const evidence = await screen.findByRole('region', { name: '数据证据' })
    expect(evidence).toBeInTheDocument()
    expect(within(evidence).getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('关键词明细')).toBeInTheDocument()
    expect(within(evidence).getByText('seo agent')).toBeInTheDocument()
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v1') ?? '[]')
    expect(stored[0].messages[1].toolResults[0].result.data.raw).toBeUndefined()
    expect(screen.getByLabelText('Agent 执行详情')).toHaveAttribute('aria-hidden', 'true')

    fireEvent.click(screen.getByRole('button', { name: /^tool 执行详情$/ }))
    expect(screen.getByLabelText('Agent 执行详情')).toHaveAttribute('aria-hidden', 'false')
    expect(screen.getAllByRole('table')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '展开工具详情 seo_organic_keywords' }))
    expect(screen.getAllByRole('table')).toHaveLength(2)
  })

  it('renders PageSpeed performance evidence inside the assistant message', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', { status: 'running' }))
      onEvent(event(2, 'tool.completed', {
        tool_call_id: 'pagespeed_1',
        name: 'seo_page_performance',
        render_hint: 'chart',
        result: {
          success: true,
          data: {
            schema_version: '1.0',
            capability: 'page_performance',
            view_type: 'chart',
            variant: 'page_performance',
            title: { key: 'seo.page_performance.title', fallback: '页面速度诊断' },
            description: {
              key: 'seo.page_performance.description',
              fallback: '查看当前页面的 Lighthouse 实验室检测结果。',
            },
            provenance: {
              source: 'pagespeed',
              source_label: 'Google PageSpeed Insights',
              is_estimated: true,
              integrity: 'complete',
              strategy: 'mobile',
              final_url: 'https://example.com/',
              lighthouse_version: '12.8.2',
            },
            limitations: ['真实用户体验需要结合 CrUX 数据判断。'],
            scores: [
              { key: 'performance', label: '性能', value: 62, status: 'needs_improvement' },
              { key: 'seo', label: 'SEO', value: 91, status: 'good' },
            ],
            metrics: [
              {
                key: 'lcp',
                label: '最大内容绘制',
                value: 3600,
                display_value: '3.6 秒',
                status: 'needs_improvement',
              },
            ],
            opportunities: [],
            diagnostics: [],
            warnings: [],
          },
        },
      }))
      onEvent(event(3, 'message.completed', { content: '首页移动端性能需要改进。' }))
      onEvent(event(4, 'run.completed', { status: 'completed' }))
    })

    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '检测首页移动端 PageSpeed' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    const evidence = await screen.findByRole('region', { name: '数据证据' })
    expect(await within(evidence).findByTestId('page-performance-result')).toBeInTheDocument()
    expect(within(evidence).getByText('页面速度诊断')).toBeInTheDocument()
    expect(within(evidence).getAllByText('Google PageSpeed Insights').length).toBeGreaterThan(0)
    expect(within(evidence).getByText('最大内容绘制')).toBeInTheDocument()
    expect(within(evidence).getByText('3.6 秒')).toBeInTheDocument()
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v1') ?? '[]')
    expect(stored[0].messages[1].toolResults[0].result.data.raw).toBeUndefined()
  })

  it('renders direct page metadata evidence for a TDK request', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', { status: 'running' }))
      onEvent(event(2, 'tool.completed', {
        tool_call_id: 'metadata_1',
        name: 'seo_page_metadata',
        render_hint: 'chart',
        result: {
          success: true,
          data: {
            schema_version: '1.0',
            capability: 'page_metadata',
            view_type: 'chart',
            variant: 'page_metadata',
            title: { key: 'seo.page_metadata.title', fallback: '页面元信息检查' },
            description: {
              key: 'seo.page_metadata.description',
              fallback: '直接读取页面源 HTML，检查 TDK 和索引控制信息。',
            },
            provenance: {
              source: 'page_html',
              source_label: '页面源 HTML',
              is_estimated: false,
              integrity: 'complete',
              final_url: 'https://example.com/',
            },
            limitations: ['不执行页面 JavaScript。'],
            http: { status_code: 200, content_type: 'text/html', final_url: 'https://example.com/' },
            indexing: { declared_noindex: false, robots: 'index,follow' },
            checks: [
              { key: 'title', label: 'Title', value: 'Example Home', length: 12, count: 1, status: 'good' },
              {
                key: 'description',
                label: 'Meta Description',
                value: 'Example description',
                length: 19,
                count: 1,
                status: 'good',
              },
              {
                key: 'keywords',
                label: 'Meta Keywords',
                value: null,
                length: 0,
                count: 0,
                status: 'info',
                note: 'Google 不使用该标签进行索引或排名。',
              },
            ],
            headings: ['Example heading'],
            additional: { charset: 'utf-8' },
            warnings: [],
          },
        },
      }))
      onEvent(event(3, 'message.completed', { content: '首页 TDK 已完成检查。' }))
      onEvent(event(4, 'run.completed', { status: 'completed' }))
    })

    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '检查当前首页的 TDK 情况' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    const evidence = await screen.findByRole('region', { name: '数据证据' })
    expect(await within(evidence).findByTestId('page-metadata-result')).toBeInTheDocument()
    expect(within(evidence).getByText('Example Home')).toBeInTheDocument()
    expect(within(evidence).getByText('Example description')).toBeInTheDocument()
    expect(within(evidence).getByText(/Google 不使用该标签/)).toBeInTheDocument()
    expect(within(evidence).queryByText(/SERP/)).not.toBeInTheDocument()
  })


  it('converts leaked ASCII business charts in the assistant message', async () => {
    const answer = `### 排名分布趋势

\`\`\`text
排名分布变化趋势（近1个月）：
Top 3（第1-3名） ██████ 64 个 → 稳定
Top 4-10（第4-10名） ██ 21 个 → 增长
排名 11+ ████████████ 129 个 → 待提升
\`\`\`

核心词整体稳定。`
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', { status: 'running' }))
      onEvent(event(2, 'message.delta', { delta: answer }))
      onEvent(event(3, 'message.completed', { content: answer }))
      onEvent(event(4, 'run.completed', { status: 'completed' }))
    })

    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '总结排名趋势' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    const summary = await screen.findByTestId('markdown-data-summary')
    expect(summary).toBeInTheDocument()
    expect(within(summary).getByText('60.3%')).toBeInTheDocument()
    expect(screen.queryByText(/██████/)).not.toBeInTheDocument()
    expect(document.querySelector('.chat-message.assistant .markdown-body pre')).toBeNull()
  })


  it('keeps the run alive after a network interruption and allows reconnecting the same run', async () => {
    streamAgentEvents
      .mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
        onEvent(event(1, 'run.started', { status: 'running' }))
        onEvent(event(2, 'tool.completed', {
          tool_call_id: 'metadata_1',
          name: 'seo_page_metadata',
          render_hint: 'chart',
          result: { success: true, data: { schema_version: '1.0', capability: 'page_metadata', view_type: 'chart', variant: 'page_metadata', title: { fallback: '页面元信息检查' }, provenance: { source: 'page_html', source_label: '页面源 HTML' }, limitations: [], checks: [] } },
        }))
        throw new Error('network error')
      })
      .mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
        onEvent(event(5, 'message.completed', { content: '任务其实已经完成。' }))
        onEvent(event(6, 'run.completed', { status: 'completed' }))
        return { lastEventId: 6, terminal: true }
      })

    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '检查页面 TDK' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    expect(await screen.findByText('连接已中断，任务可能仍在后台执行')).toBeInTheDocument()
    expect(screen.queryByText('执行失败')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新连接' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新连接' }))

    expect(await screen.findByText('任务其实已经完成。')).toBeInTheDocument()
    expect(streamAgentEvents).toHaveBeenCalledTimes(2)
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v1') ?? '[]')
    expect(stored[0].messages.filter((item: { runId?: string }) => item.runId === 'run_1')).toHaveLength(1)
  })


  it('hides the Agent task sidebar and keeps tool details collapsed by default', async () => {
    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给 SEO 优化专员发送消息'), {
      target: { value: '分析自然流量下降' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await screen.findByText('分析完成')
    expect(screen.queryByRole('region', { name: 'Agent 任务' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^tool 执行详情$/ }))
    const toggle = screen.getByRole('button', { name: '展开工具详情 seo_organic_keywords' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    const inspector = screen.getByLabelText('Agent 执行详情')
    expect(within(inspector).queryByText('seo agent')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(within(inspector).getByText('seo agent')).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

})
