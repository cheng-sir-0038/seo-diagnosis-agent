import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '../pages/ChatPage'
import type { AgentEvent } from '../types/agent'

const createAgentRun = vi.fn()
const streamAgentEvents = vi.fn()
const cancelAgentRun = vi.fn()
const listAgentTasks = vi.fn()
const getAgentRun = vi.fn()
const resumeAgentRun = vi.fn()
const submitAgentInput = vi.fn()

vi.mock('../services/agentApi', () => ({
  createAgentRun: (...args: unknown[]) => createAgentRun(...args),
  streamAgentEvents: (...args: unknown[]) => streamAgentEvents(...args),
  cancelAgentRun: (...args: unknown[]) => cancelAgentRun(...args),
  listAgentTasks: (...args: unknown[]) => listAgentTasks(...args),
  getAgentRun: (...args: unknown[]) => getAgentRun(...args),
  resumeAgentRun: (...args: unknown[]) => resumeAgentRun(...args),
  submitAgentInput: (...args: unknown[]) => submitAgentInput(...args),
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
    window.sessionStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
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
      onEvent(event(2, 'tool.started', {
        tool_call_id: 'call_1',
        name: 'seo_organic_keywords',
        arguments: { domain: 'example.com', limit: 5 },
      }))
      onEvent(event(3, 'tool.completed', {
        tool_call_id: 'call_1',
        name: 'seo_organic_keywords',
        render_hint: 'table',
        latency_ms: 2500,
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
      onEvent(event(4, 'message.delta', { delta: '分析完成' }))
      onEvent(event(5, 'message.completed', { content: '分析完成' }))
      onEvent(event(6, 'run.completed', { status: 'completed' }))
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('reopens the original run and restores the reply after a reload during generation', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', { status: 'running' }))
      return new Promise(() => {})
    })
    const first = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), {
      target: { value: '核对首页索引设置' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('生成中')
    const originalSignal = streamAgentEvents.mock.calls[0][2].signal
    first.unmount()

    const reloaded = render(<ChatPage />)
    expect(await screen.findByText('分析完成')).toBeInTheDocument()
    expect(originalSignal.aborted).toBe(true)
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(streamAgentEvents.mock.calls[1][0]).toBe('/api/v1/seo-agent/chat/runs/run_1/events')
    expect(streamAgentEvents.mock.calls[1][2].afterEventId).toBe(0)
    expect(await screen.findAllByText('关键词明细')).toHaveLength(1)

    reloaded.unmount()
    render(<ChatPage />)
    expect(await screen.findByText('分析完成')).toBeInTheDocument()
    expect(streamAgentEvents).toHaveBeenCalledTimes(2)
  })

  it('keeps the human-input request and its continuation cursor across reloads', async () => {
    const medical = { agent: 'healthcare', agent_run_id: 'medical' }
    const seo = { agent: 'seo', agent_run_id: 'page' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', medical))
      onEvent(event(3, 'message.completed', { content: '这个页面主要提供什么内容？' }))
      onEvent(event(4, 'run.waiting_for_input', { ...medical, request_id: 'input_1', questions: ['这个页面主要提供什么内容？'] }))
      return { lastEventId: 3, terminal: true }
    })
    const first = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '根据页面给我一份建议' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('进度已保存，补充后继续本次分析。')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: '专员进度' })).toHaveTextContent('医药业务待补充')
    first.unmount()
    submitAgentInput.mockResolvedValue({ run_id: 'run_1', task_id: 'agtask_12345678', thread_id: 'chat_12345678',
      status: 'recovering', events_url: '/api/v1/seo-agent/chat/runs/run_1/events', cancel_url: '/cancel', after_sequence: 4 })
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(5, 'run.resumed', {}))
      return new Promise(() => {})
    })
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(5, 'run.resumed', {}))
      onEvent(event(6, 'agent.dispatched', medical))
      onEvent(event(7, 'agent.completed', medical))
      onEvent(event(8, 'agent.dispatched', seo))
      onEvent(event(9, 'agent.completed', seo))
      onEvent(event(10, 'message.completed', { content: '建议已整理，请人工确认。' }))
      onEvent(event(11, 'run.completed', {}))
      return { lastEventId: 11, terminal: true }
    })
    const continuation = render(<ChatPage />)
    expect(screen.getByText('进度已保存，补充后继续本次分析。')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '这里汇总野外观察笔记' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('恢复中')
    continuation.unmount()

    const second = render(<ChatPage />)
    const answer = await screen.findByText('建议已整理，请人工确认。')
    const participants = within(answer.closest('article')!).getByRole('list', { name: '专员进度' })
    expect(within(participants).getAllByRole('listitem').map(item => item.textContent))
      .toEqual(['医药业务已完成', 'SEO / GEO已完成'])
    expect(screen.getByText('这个页面主要提供什么内容？')).toBeInTheDocument()
    expect(submitAgentInput).toHaveBeenCalledWith('run_1', 'input_1', '这里汇总野外观察笔记', 'answer')
    expect(submitAgentInput).toHaveBeenCalledTimes(1)
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(streamAgentEvents.mock.calls.at(-1)?.[2].afterEventId).toBe(4)
    second.unmount()
    render(<ChatPage />)
    const saved = await screen.findByText('建议已整理，请人工确认。')
    expect(within(saved.closest('article')!).getByRole('list', { name: '专员进度' }))
      .toHaveTextContent('医药业务已完成SEO / GEO已完成')
  })

  it('renders a math exercise and submits the choice to the same run after reload', async () => {
    const coach = { agent: 'learning', agent_run_id: 'practice' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', coach))
      onEvent(event(3, 'skill.loaded', {
        ...coach, skill: 'math-practice', label: '一元一次方程练习',
      }))
      onEvent(event(4, 'tool.completed', {
        ...coach, tool_call_id: 'question_1', name: 'tal_math_practice',
      }))
      onEvent(event(5, 'message.completed', { content: '### 第 1 / 1 题\n\n设 $\\frac{x}{2}=3$。' }))
      onEvent(event(6, 'run.waiting_for_input', {
        ...coach, request_id: 'choice_1', questions: ['回复一个选项字母。'],
      }))
      return { lastEventId: 6, terminal: true }
    })
    const first = render(<ChatPage />)
    fireEvent.click(screen.getByRole('button', { name: /数学陪练/ }))
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const heading = await screen.findByRole('heading', { name: '第 1 / 1 题' })
    expect(heading.closest('.markdown-body')?.querySelector('.katex')).not.toBeNull()
    expect(screen.getByRole('list', { name: '专员进度' })).toHaveTextContent('学习陪练待补充')
    first.unmount()

    submitAgentInput.mockResolvedValue({
      run_id: 'run_1', status: 'recovering', events_url: '/events', cancel_url: '/cancel', after_sequence: 7,
    })
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(8, 'run.resumed', {}))
      onEvent(event(9, 'agent.completed', coach))
      onEvent(event(10, 'message.completed', { content: '答对了。本组共答对 1 道。' }))
      onEvent(event(11, 'run.completed', {}))
      return { lastEventId: 11, terminal: true }
    })
    render(<ChatPage />)
    const composer = screen.getByPlaceholderText('输入选项字母、跳过或结束练习')
    fireEvent.change(composer, { target: { value: 'B' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('答对了。本组共答对 1 道。')).toBeInTheDocument()
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(submitAgentInput).toHaveBeenCalledWith('run_1', 'choice_1', 'B', 'answer')
  })

  it('shows the procurement source and price gap in the result card', async () => {
    const agent = { agent: 'shopping_procurement', agent_run_id: 'procurement' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', agent))
      onEvent(event(3, 'tool.completed', {
        ...agent, tool_call_id: 'compare', name: 'shopping_compare_quotes', render_hint: 'cards',
        result: { success: true, data: {
          view_type: 'cards', items: [{
            key: 'quote-0', title: '纸杯 · 甲商家', summary: '确认运费',
            metadata: [{ label: '已知小计', value: '840.00 元' }],
          }, {
            key: 'source-0', title: '淘宝联盟', summary: '可以先使用你提供的资料。',
          }],
        } },
      }))
      onEvent(event(4, 'message.completed', { content: '采购结果已整理。' }))
      onEvent(event(5, 'run.waiting_for_input', {
        ...agent, request_id: 'other_quote', questions: ['可以继续补充报价。'],
      }))
      return { lastEventId: 5, terminal: true }
    })
    render(<ChatPage />)
    fireEvent.click(screen.getByRole('button', { name: /列活动采购清单/ }))
    const composer = screen.getByRole('textbox', { name: '给业务助理发送消息' })
    expect((composer as HTMLTextAreaElement).value).toContain('20 人')
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const report = within(await screen.findByTestId('card-result'))
    expect(report.getByText('确认运费')).toBeVisible()
    expect(report.getByText('840.00 元')).toBeVisible()
    expect(report.getByText('淘宝联盟')).toBeVisible()
    expect(screen.getByRole('list', { name: '专员进度' })).toHaveTextContent('购物与采购待补充')
  })

  it('shows destination weather and preparation tips before a trip has dates', async () => {
    const specialist = { agent: 'travel', agent_run_id: 'trip' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', specialist))
      onEvent(event(3, 'tool.completed', {
        ...specialist, tool_call_id: 'hangzhou_forecast', name: 'travel_weather',
        result: { success: true, data: {
          source: 'open_meteo', status: 'ok', scope: 'near_term', location: '杭州',
          items: [{ date: '2026-11-23', precipitation_probability_max_pct: 70 }],
          advice: ['2026-11-23 降水概率最高 70%，建议带伞。'],
        } },
      }))
      onEvent(event(4, 'message.completed', { content: '你准备哪天出发和返程？' }))
      onEvent(event(5, 'run.waiting_for_input', {
        ...specialist, request_id: 'trip_dates', questions: ['你准备哪天出发和返程？'],
      }))
      return { lastEventId: 5, terminal: true }
    })
    render(<ChatPage />)
    const composer = screen.getByRole('textbox', { name: '给业务助理发送消息' })
    fireEvent.change(composer, { target: { value: '我去杭州出差。' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    const forecast = within(await screen.findByRole('region', { name: '近期天气参考' }))
    expect(forecast.getByRole('heading', { name: '近期天气参考 · 杭州' })).toBeVisible()
    expect(forecast.getByText('70% 降水概率')).toBeVisible()
    expect(forecast.getByRole('list', { name: '出行建议' })).toHaveTextContent('建议带伞')
    expect(forecast.getByRole('link', { name: 'Open-Meteo' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '出差方案比较' })).toBeNull()
  })

  it('uses the latest travel comparison and waits for the user to send the selection', async () => {
    const specialist = { agent: 'travel', agent_run_id: 'trip' }
    const bookingUrl = 'https://example.com/offer?ref=original&ticket=1'
    const queriedAt = '2026-11-23T08:00:00+08:00'
    const data = {
      request: {
        origin: '上海', destination: '武汉', depart_on: '2026-12-01', return_on: '2026-12-03',
        venue: '武汉国际博览中心', arrive_by: '15:00', budget_cny: 3500, rooms: 1, travelers: 2, buffer_minutes: 30,
      },
      revision: 2,
      plans: [{
        id: 'plan_hash', label: '高铁与会场附近住宿', transport: 'train-1', return_transport: null, hotel: 'hotel-1',
        known_cost: '1200.00', currency: 'CNY', complete_price: false, hotel_commute_minutes: 22,
        arrival_commute_minutes: 35, arrival_status: 'met',
        missing: ['返程价格未返回'], warnings: ['房价按每晚显示，尚未取得含税总价'],
      }],
      offers: [
        {
          id: 'train-1', kind: 'transport', mode: 'rail', name: 'G123 次列车', source: 'flyai', from_stop: '上海虹桥',
          to_stop: '武汉站', departs_at: '2026-12-01T08:15:00+08:00', arrives_at: '2026-12-01T12:20:00+08:00',
          amount: '600.00', currency: 'CNY', price_basis: 'person', booking_url: bookingUrl, queried_at: queriedAt,
        },
        { id: 'hotel-1', kind: 'hotel', name: '会场附近酒店', source: 'flyai', booking_url: 'javascript:alert(1)' },
      ],
      issues: ['跨平台报价尚未核对'],
      providers: [{ source: 'ctrip', status: 'unconfigured', message: '未配置授权' }],
      weather: { source: 'open_meteo', status: 'ok', location: '武汉', timezone: 'Asia/Shanghai', queried_at: queriedAt,
        items: [{ date: '2026-12-01', temperature_min_c: 5, temperature_max_c: 12, precipitation_probability_max_pct: 65 }],
        advice: ['低温出行，建议带外套。'],
      },
    }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', specialist))
      onEvent(event(3, 'tool.completed', {
        ...specialist, tool_call_id: 'first_comparison', name: 'travel_compare_plans', render_hint: 'cards',
        result: { success: true, data: { ...data, revision: 1, plans: [], issues: ['旧报价结果'] } },
      }))
      onEvent(event(4, 'tool.completed', {
        ...specialist, tool_call_id: 'comparison', name: 'travel_compare_plans', render_hint: 'cards',
        result: { success: true, data },
      }))
      onEvent(event(5, 'message.completed', { content: '已对照交通和住宿。选一个方案，或告诉我新的会议时间。' }))
      onEvent(event(6, 'run.waiting_for_input', { ...specialist, request_id: 'choose_plan', questions: ['要保留哪个方案？'] }))
      return { lastEventId: 6, terminal: true }
    })
    submitAgentInput.mockResolvedValue({
      run_id: 'run_1', status: 'recovering', events_url: '/events', cancel_url: '/cancel', after_sequence: 6,
    })
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(7, 'run.resumed', {}))
      onEvent(event(8, 'tool.completed', {
        ...specialist, tool_call_id: 'saved_plan', name: 'travel_save_plan', render_hint: 'cards',
        result: { success: true, data: { ...data, selected_plan_id: 'plan_hash' } },
      }))
      onEvent(event(9, 'agent.completed', specialist))
      onEvent(event(10, 'run.completed', {}))
      return { lastEventId: 10, terminal: true }
    })

    render(<ChatPage />)
    fireEvent.click(screen.getByRole('button', { name: /比较出差方案/ }))
    const composer = screen.getByRole('textbox', { name: '给业务助理发送消息' })
    expect((composer as HTMLTextAreaElement).value).toContain('上海到武汉')
    expect(createAgentRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    const result = within(await screen.findByRole('region', { name: '出差方案比较' }))
    expect(result.getByText('返程价格未返回')).toBeInTheDocument()
    expect(result.getByText('跨平台报价尚未核对')).toBeInTheDocument()
    expect(result.getByText('房价按每晚显示，尚未取得含税总价')).toBeInTheDocument()
    expect(result.getByText('65% 降水概率', { exact: false })).toBeInTheDocument()
    expect(result.getByRole('list', { name: '出行建议' })).toHaveTextContent('带外套')
    expect(result.getByText('已知费用')).toBeInTheDocument()
    expect(result.getByText('酒店到会场约 22 分钟')).toBeInTheDocument()
    expect(result.getByText('抵达后到会场约 35 分钟')).toBeInTheDocument()
    fireEvent.click(result.getByText('候选与来源'))
    expect(result.getByRole('link', { name: /G123 次列车/ })).toHaveAttribute('href', bookingUrl)
    expect(result.queryByRole('link', { name: /会场附近酒店/ })).toBeNull()
    expect(screen.queryByText('旧报价结果')).toBeNull()
    expect(screen.getByRole('list', { name: '专员进度' })).toHaveTextContent('商旅行程待补充')

    fireEvent.click(result.getByRole('button', { name: '选择方案 1' }))
    expect(screen.getByRole('textbox', { name: '给业务助理发送消息' })).toHaveValue('保存方案 1')
    expect(submitAgentInput).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('已保存为行程草稿')).toBeInTheDocument()
    expect(submitAgentInput).toHaveBeenCalledWith('run_1', 'choose_plan', '保存方案 1', 'answer')
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: '选择方案 1' })).toBeNull()
  })

  it('exposes the travel source gap when no plan can be built', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'tool.completed', {
        agent: 'travel', tool_call_id: 'empty_comparison', name: 'travel_compare_plans',
        result: { success: true, data: {
          request: { origin: '上海', destination: '武汉' },
          plans: [], issues: ['未取得交通候选'],
          providers: [{ source: 'flyai', status: 'unconfigured', message: '尚未配置飞猪查询。' }],
        } },
      }))
      onEvent(event(3, 'run.waiting_for_input', { request_id: 'travel_quotes', questions: ['请补充交通报价。'] }))
      return { lastEventId: 3, terminal: true }
    })
    render(<ChatPage />)
    fireEvent.click(screen.getByRole('button', { name: /比较出差方案/ }))
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const result = within(await screen.findByRole('region', { name: '出差方案比较' }))
    expect(result.getByText('未取得交通候选')).toBeInTheDocument()
    expect(result.getByText('尚未配置飞猪查询。', { exact: false })).toBeVisible()
    expect(result.queryByRole('button', { name: /选择方案/ })).toBeNull()
  })

  it('lets the user explicitly finish with available facts', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'message.completed', { content: '请提供分享图片地址。' }))
      onEvent(event(2, 'run.waiting_for_input', { request_id: 'input_skip', questions: ['分享图片？'], allow_use_available: true }))
      return { lastEventId: 2, terminal: true }
    })
    submitAgentInput.mockResolvedValue({ run_id: 'run_1', status: 'recovering', events_url: '/events', cancel_url: '/cancel', after_sequence: 3 })
    render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '给当前页面提建议' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    fireEvent.click(await screen.findByRole('button', { name: '先用现有信息生成' }))
    await waitFor(() => expect(submitAgentInput).toHaveBeenCalledWith('run_1', 'input_skip', expect.any(String), 'use_available'))
    expect(createAgentRun).toHaveBeenCalledTimes(1)
  })

  it('replaces the saved partial reply after recovering the same user turn', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'tool.completed', {tool_call_id:'saved',name:'seo_page_metadata',result:{success:true,data:{}}}))
      onEvent(event(3, 'run.failed', {message:'summary failed'}))
      return {lastEventId:3,terminal:true}
    }).mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(5, 'run.resumed', {}))
      onEvent(event(6, 'message.completed', {content:'恢复后的完整结论。'}))
      onEvent(event(7, 'run.completed', {}))
      return {lastEventId:7,terminal:true}
    })
    getAgentRun.mockResolvedValue({id:'run_1',status:'FAILED'})
    const {container} = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', {name:'给业务助理发送消息'}), {target:{value:'给页面提建议'}})
    fireEvent.click(screen.getByRole('button', {name:'发送消息'}))
    expect(await screen.findByText('已保留本次取得的数据，见下方。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', {name:'基于已有证据完成'}))
    expect(await screen.findByText('恢复后的完整结论。')).toBeInTheDocument()
    await waitFor(() => expect(container.querySelectorAll('.chat-message.assistant')).toHaveLength(1))
    expect(screen.queryByText('已保留本次取得的数据，见下方。')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows which specialist ran even when its first operation fails', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      const specialist = { agent: 'healthcare', agent_run_id: 'medical' }
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', specialist))
      onEvent(event(3, 'agent.failed', specialist))
      onEvent(event(4, 'run.failed', { message: '资料暂时不可用' }))
      return { lastEventId: 4, terminal: true }
    })
    render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '帮我查考试大纲' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const participants = await screen.findByRole('list', { name: '专员进度' })
    expect(participants).toHaveTextContent('医药业务失败')
    expect(within(participants.closest('article')!).getByText('本轮执行中断。')).toBeInTheDocument()
  })

  it('keeps the actual tool with the specialist who used it when the conversation is reopened', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      const medical = { agent: 'healthcare', agent_run_id: 'medical' }
      const seo = { agent: 'seo', agent_run_id: 'page' }
      onEvent(event(1, 'agent.dispatched', medical))
      onEvent(event(2, 'skill.loaded', {
        ...medical, skill: 'medical-exam-syllabus', label: '医考考纲核对',
      }))
      onEvent(event(3, 'agent.dispatched', seo))
      onEvent(event(4, 'tool.completed', { ...medical, tool_call_id: 'rule', name: 'medical_exam_syllabus' }))
      onEvent(event(5, 'tool.completed', { ...seo, tool_call_id: 'page', name: 'seo_page_metadata' }))
      onEvent(event(6, 'agent.completed', medical))
      onEvent(event(7, 'agent.completed', seo))
      onEvent(event(8, 'message.completed', { content: '两位专员核对完成。' }))
      onEvent(event(9, 'run.completed', {}))
      return { lastEventId: 9, terminal: true }
    })
    const first = render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value: '检查课程页的说法' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('两位专员核对完成。')
    first.unmount()
    render(<ChatPage />)
    const list = within(screen.getByRole('list', { name: '专员进度' })).getAllByRole('listitem')
    expect(list).toHaveLength(2)
    expect(list[0]).toHaveTextContent('医药业务已完成Skill · 医考考纲核对查证考纲条款')
    expect(list[1]).toHaveTextContent('SEO / GEO已完成读取网页内容')
    fireEvent.click(screen.getByText('执行详情'))
    const medicalDetails = await screen.findByRole('region', { name: '医药业务' })
    const seoDetails = screen.getByRole('region', { name: 'SEO / GEO' })
    expect(within(medicalDetails).getByText('查证考纲条款')).toBeInTheDocument()
    expect(within(seoDetails).getByText('读取网页内容')).toBeInTheDocument()
  })

  it('fills a relevant starter without running a query until the user sends it', () => {
    render(<ChatPage />)
    fireEvent.click(screen.getByRole('button', { name: /页面检查/ }))
    const prompt = (screen.getByLabelText('给业务助理发送消息') as HTMLTextAreaElement).value
    expect(prompt).toContain('https://breadtopia.com/how-to-make-a-sourdough-starter/')
    expect(prompt).toContain('标题')
    expect(createAgentRun).not.toHaveBeenCalled()
    expect(screen.getByLabelText('发送消息')).toBeEnabled()
  })

  it('shows an honest activity state while the first response is still pending', async () => {
    createAgentRun.mockImplementationOnce(() => new Promise(() => {}))
    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '检查本站首页' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))
    expect(await screen.findByRole('status', { name: '分析进度' })).toHaveTextContent('正在准备任务')
    expect(screen.getByLabelText('停止生成')).toBeEnabled()
    expect(screen.queryByRole('region', { name: '本次结果' })).not.toBeInTheDocument()
  })

  it('sends the current conversation and renders structured evidence directly in the chat', async () => {
    render(<ChatPage />)

    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '分析自然流量下降' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await waitFor(() => expect(createAgentRun).toHaveBeenCalled())
    expect(createAgentRun.mock.calls[0][1]).toBe(sessionStorage.getItem('agent-workspace-active-conversation-v2'))
    const answer = await screen.findByText('分析完成')
    const reply = answer.closest('article')!
    const evidence = within(reply).getByRole('region', { name: '本次结果' })
    expect(evidence).toBeInTheDocument()
    expect(await within(evidence).findByRole('table')).toBeInTheDocument()
    expect(screen.getByText('关键词明细')).toBeInTheDocument()
    expect(within(evidence).getByText('seo agent')).toBeInTheDocument()
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v2') ?? '[]')
    expect(stored[0].messages[1].toolResults[0].result.data.raw).toBeUndefined()
    expect(screen.queryByText('Task 12345678 · Thread 12345678')).not.toBeInTheDocument()
    expect(screen.queryByText('数据证据', { exact: true })).not.toBeInTheDocument()
    expect(within(reply).getByText('执行详情').closest('details')).not.toHaveAttribute('open')
    expect(screen.getAllByRole('table')).toHaveLength(1)
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
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '检测首页移动端 PageSpeed' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    const evidence = await screen.findByRole('region', { name: '本次结果' })
    expect(await within(evidence).findByTestId('page-performance-result')).toBeInTheDocument()
    expect(within(evidence).getByText('页面速度诊断')).toBeInTheDocument()
    expect(within(evidence).getAllByText('Google PageSpeed Insights').length).toBeGreaterThan(0)
    expect(within(evidence).getByText('最大内容绘制')).toBeInTheDocument()
    expect(within(evidence).getByText('3.6 秒')).toBeInTheDocument()
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v2') ?? '[]')
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
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '检查当前首页的 TDK 情况' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    const evidence = await screen.findByRole('region', { name: '本次结果' })
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
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
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
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
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
    const stored = JSON.parse(window.localStorage.getItem('agent-workspace-conversations-v2') ?? '[]')
    expect(stored[0].messages.filter((item: { runId?: string }) => item.runId === 'run_1')).toHaveLength(1)
  })


  it('keeps execution details collapsed inside the reply and opens raw data on demand', async () => {
    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '分析自然流量下降' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await screen.findByText('分析完成')
    expect(screen.queryByLabelText('Agent 执行详情')).not.toBeInTheDocument()
    const details = screen.getByText('执行详情').closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument()

    details.open = true
    fireEvent(details, new Event('toggle'))
    expect(await within(details).findByText('关键词明细')).toBeInTheDocument()
    expect(within(details).getByText('domain')).toBeInTheDocument()
    expect(within(details).getByText('example.com')).toBeInTheDocument()
    expect(within(details).getByText('2.5 秒')).toBeInTheDocument()
    expect(within(details).queryByText('run.started')).not.toBeInTheDocument()
    expect(within(details).queryByText('tool.completed')).not.toBeInTheDocument()

    const result = within(details).getByText('返回数据').closest('details')!
    expect(result).not.toHaveAttribute('open')
    result.open = true
    fireEvent(result, new Event('toggle'))
    expect(await within(result).findByTestId('raw-tool-result')).toHaveTextContent('seo agent')
    expect(screen.getAllByRole('table')).toHaveLength(1)

    details.open = false
    fireEvent(details, new Event('toggle'))
    await waitFor(() => expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument())
  })

})
