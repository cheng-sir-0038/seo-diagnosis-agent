import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '../pages/ChatPage'
import type { CreateRunResponse } from '../services/agentApi'
import type { AgentEvent, Conversation } from '../types/agent'

const createAgentRun = vi.fn()
const streamAgentEvents = vi.fn()
const cancelAgentRun = vi.fn()
const listAgentTasks = vi.fn()
const listWorkspaceTasks = vi.fn()
const getAgentRun = vi.fn()
const resumeAgentRun = vi.fn()
const submitAgentInput = vi.fn()

vi.mock('../services/agentApi', () => ({
  agentApiUrl: (path: string) => path,
  createAgentRun: (...args: unknown[]) => createAgentRun(...args),
  streamAgentEvents: (...args: unknown[]) => streamAgentEvents(...args),
  cancelAgentRun: (...args: unknown[]) => cancelAgentRun(...args),
  listAgentTasks: (...args: unknown[]) => listAgentTasks(...args),
  listWorkspaceTasks: (...args: unknown[]) => listWorkspaceTasks(...args),
  getAgentRun: (...args: unknown[]) => getAgentRun(...args),
  getWorkspaceRunStats: () => Promise.resolve({ elapsed_ms: null, total_tokens: null }),
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

async function openExecutionDrawer() {
  fireEvent.click(screen.getByRole('button', { name: '打开执行详情' }))
  return within(await screen.findByRole('dialog', { name: '执行详情' }))
}

function changeDisclosure(details: HTMLDetailsElement, open: boolean) {
  details.open = open
  fireEvent(details, new Event('toggle'))
}

describe('ChatPage SEO Agent integration', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    window.history.replaceState(null, '', '/')
    listWorkspaceTasks.mockReset().mockResolvedValue([])
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
    vi.unstubAllGlobals()
  })

  it('follows new output at the bottom, respects reading history, and keeps the reply at completion', async () => {
    const NativeResizeObserver = ResizeObserver
    let notifyResize = () => {}
    vi.stubGlobal('ResizeObserver', class extends NativeResizeObserver {
      constructor(private readonly callback: ResizeObserverCallback) { super(callback) }
      observe(target: Element) {
        super.observe(target)
        if (target.classList.contains('chat-message-list')) notifyResize = () => this.callback([], this)
      }
    })
    let publish!: (item: AgentEvent) => void
    let finish!: () => void
    streamAgentEvents.mockImplementationOnce((_url, onEvent: (item: AgentEvent) => void) =>
      new Promise(resolve => {
        publish = onEvent
        finish = () => resolve({ lastEventId: 3, terminal: true })
      }),
    )

    const { container } = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '核对页面' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(publish).toBeDefined())

    const messages = container.querySelector<HTMLDivElement>('.chat-messages')!
    let scrollTop = 200
    let scrollHeight = 600
    Object.defineProperties(messages, {
      clientHeight: { value: 400 },
      scrollHeight: { get: () => scrollHeight },
      scrollTop: { get: () => scrollTop, set: (top: number) => { scrollTop = Math.min(top, scrollHeight - 400) } },
    })
    act(() => {
      publish(event(1, 'run.started', {}))
      publish(event(2, 'message.delta', { delta: '已核对页面。' }))
    })
    expect(await screen.findByText('已核对页面。')).toBeVisible()
    scrollHeight += 120
    notifyResize()
    expect(messages.scrollTop).toBe(320)

    messages.scrollTop = 50
    fireEvent.scroll(messages)
    scrollHeight += 100
    notifyResize()
    expect(messages.scrollTop).toBe(50)
    expect(screen.getByRole('button', { name: '返回最新消息' })).toBeVisible()

    act(() => publish(event(3, 'run.completed', { status: 'completed' })))
    expect(screen.getByText('已核对页面。')).toBeVisible()
    await act(async () => finish())
    expect(screen.getAllByText('已核对页面。')).toHaveLength(1)
    expect(messages.scrollTop).toBe(50)
    fireEvent.click(screen.getByRole('button', { name: '返回最新消息' }))
    expect(messages.scrollTop).toBe(scrollHeight - 400)
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
    await screen.findByText('进行中')
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

  it('shows a registered specialist and its Skill in chat, history and tasks', async () => {
    const archive = { agent: 'archive_review', agent_run_id: 'archive' }
    const profile = { name: '档案核验', icon: 'search', tone: 'indigo', directory_tone: 'learning' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', archive))
      onEvent(event(3, 'skill.loaded', { ...archive, skill: 'archive-scan', label: '核对档案', agent_profile: profile }))
      onEvent(event(4, 'tool.completed', { ...archive, tool_call_id: 'read', name: 'archive_read', result: { present: true } }))
      onEvent(event(5, 'agent.completed', archive))
      onEvent(event(6, 'message.completed', { content: '档案已经核验。' }))
      onEvent(event(7, 'run.completed', {}))
      return { lastEventId: 7, terminal: true }
    })
    const first = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '检查档案清单' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const answer = await screen.findByText('档案已经核验。')
    expect(within(answer.closest('article')!).getByRole('list', { name: '专员进度' })).toHaveTextContent('档案核验已完成')
    first.unmount()

    const conversationId = createAgentRun.mock.calls[0][1]
    const now = new Date().toISOString()
    listWorkspaceTasks.mockResolvedValue([{
      id: 'task-archive', conversation_id: conversationId, current_run_id: 'run_1', title: '检查档案清单',
      status: 'COMPLETED', created_at: now, updated_at: now,
    }])
    window.history.replaceState(null, '', '/#/tasks')
    render(<ChatPage />)
    const taskList = within(screen.getByRole('region', { name: '任务列表' }))
    const task = await taskList.findByRole('button', { name: /检查档案清单/ })
    expect(task.querySelector('[data-icon="search"]')).toBeVisible()
    await openHistoryConversation('检查档案清单')
    const saved = await screen.findByText('档案已经核验。')
    expect(within(saved.closest('article')!).getByRole('list', { name: '专员进度' })).toHaveTextContent('档案核验已完成')
    const drawer = await openExecutionDrawer()
    expect(drawer.getByRole('list', { name: '本轮执行链路' })).toHaveTextContent('核对档案 · 1 次工具调用')
    expect(drawer.getByRole('region', { name: '档案核验' })).toHaveTextContent('archive_read')
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

  it('keeps a saved answer after a scheduling error and recovers its own reply', async () => {
    const firstTurn = [
      event(1, 'run.started', {}),
      event(2, 'message.completed', { content: '### 第一道题\n\n求解 x + 2 = 3。' }),
      event(3, 'run.waiting_for_input', { request_id: 'question-1', questions: ['请回复第 1 题选项。'] }),
    ]
    const receipt = event(4, 'input.submitted', { request_id: 'question-1', answer: 'A', action: 'answer' })
    const secondTurn = [
      event(5, 'run.resumed', {}),
      event(6, 'message.completed', { content: '第 1 题答对了。\n\n### 第二道题\n\n求解 2x = 4。' }),
      event(7, 'run.waiting_for_input', { request_id: 'question-2', questions: ['请回复第 2 题选项。'] }),
    ]
    streamAgentEvents.mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      firstTurn.forEach(publish)
      return { lastEventId: 3, terminal: true }
    }).mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      secondTurn.forEach(publish)
      return { lastEventId: 7, terminal: true }
    })
    submitAgentInput.mockImplementationOnce(async () => {
      getAgentRun.mockResolvedValue({
        id: 'run_1', status: 'RECOVERY_BLOCKED', checkpoint: { id: 'checkpoint-1' },
        error_message: '调度暂不可用', events: [...firstTurn, receipt],
      })
      throw Object.assign(new Error('补充已保存，运行暂时无法恢复'), { status: 503 })
    })
    resumeAgentRun.mockImplementationOnce(async () => {
      getAgentRun.mockResolvedValue({
        id: 'run_1', status: 'WAITING_FOR_INPUT', checkpoint: { id: 'checkpoint-2' },
        events: [...firstTurn, receipt, ...secondTurn],
      })
      return { run_id: 'run_1', status: 'recovering' }
    })

    const first = render(<ChatPage />)
    const answer = () => within(screen.getByRole('region', { name: '对话消息' })).getByText('A')
    const send = (value: string) => {
      fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    }
    send('请出两道方程题')
    expect(await screen.findByRole('heading', { name: '第一道题' })).toBeInTheDocument()
    send('A')
    expect(await screen.findByRole('button', { name: '恢复任务' })).toBeInTheDocument()
    expect(answer()).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '给业务助理发送消息' })).toHaveValue('')
    expect(first.container.querySelectorAll('.chat-message.assistant')).toHaveLength(1)

    first.unmount()
    const second = render(<ChatPage />)
    fireEvent.click(await screen.findByRole('button', { name: '恢复任务' }))
    expect(await screen.findByRole('heading', { name: '第二道题' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '第一道题' })).toBeInTheDocument()
    expect(screen.getAllByText('第 1 题答对了。')).toHaveLength(1)
    expect(answer()).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '给业务助理发送消息' })).toHaveValue('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(submitAgentInput).toHaveBeenCalledTimes(1)
    expect(resumeAgentRun).toHaveBeenCalledWith('run_1')
    expect(streamAgentEvents.mock.calls.at(-1)?.[2].afterEventId).toBe(4)

    second.unmount()
    render(<ChatPage />)
    expect(await screen.findByRole('heading', { name: '第二道题' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '第一道题' })).toBeInTheDocument()
    expect(answer()).toBeInTheDocument()
  })

  it('keeps an unconfirmed answer across reload and restores its draft only after checking the same question', async () => {
    const waiting = event(2, 'run.waiting_for_input', { request_id: 'original-question', questions: ['请补充规格。'] })
    streamAgentEvents.mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      publish(event(1, 'message.completed', { content: '请先确认采购条件。' }))
      publish(waiting)
      return { lastEventId: 2, terminal: true }
    })
    submitAgentInput.mockImplementationOnce(async () => {
      getAgentRun.mockRejectedValue(new Error('读取暂时失败'))
      throw new TypeError('连接中断')
    })
    const title = '给活动买纸杯'
    const answer = '200 ml'
    const composer = () => screen.getByRole('textbox', { name: '给业务助理发送消息' })
    const send = () => screen.getByRole('button', { name: '发送消息' })
    const first = render(<ChatPage />)
    fireEvent.change(composer(), { target: { value: title } })
    fireEvent.click(send())
    expect(await screen.findByText('请补充规格。')).toBeInTheDocument()
    fireEvent.change(composer(), { target: { value: answer } })
    fireEvent.click(send())
    expect(await screen.findByRole('button', { name: '重新核对' })).toBeInTheDocument()
    expect(composer()).toBeDisabled()
    expect(composer()).toHaveValue('')

    first.unmount()
    render(<ChatPage />)
    const recheck = await screen.findByRole('button', { name: '重新核对' })
    const transcript = within(screen.getByRole('region', { name: '对话消息' }))
    expect(transcript.getByText(answer)).toBeInTheDocument()
    getAgentRun.mockResolvedValue({
      id: 'run_1', status: 'WAITING_FOR_INPUT', events: [waiting],
    })
    fireEvent.click(recheck)
    await waitFor(() => expect(composer()).toHaveValue(answer))
    expect(composer()).toBeEnabled()
    expect(transcript.queryByText(answer)).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('补充尚未保存')
    expect(submitAgentInput).toHaveBeenCalledTimes(1)
    expect(resumeAgentRun).not.toHaveBeenCalled()
  })

  it('shows procurement requirements, amounts and sources in separate tables', async () => {
    const agent = { agent: 'shopping_procurement', agent_run_id: 'procurement' }
    streamAgentEvents.mockImplementationOnce(async (_url, onEvent: (item: AgentEvent) => void) => {
      onEvent(event(1, 'run.started', {}))
      onEvent(event(2, 'agent.dispatched', agent))
      onEvent(event(3, 'tool.completed', {
        ...agent, tool_call_id: 'compare', name: 'shopping_compare_quotes', render_hint: 'cards',
        result: { success: true, data: {
          view_type: 'cards', capability: 'shopping_procurement', items: [{
            key: 'line-0', title: '纸杯', metadata: [
              { label: '需求规格', value: '200ml' }, { label: '需求数量', value: '待明确' },
            ],
          }, {
            key: 'quote-0', title: '纸杯 · 甲商家', summary: '确认运费；确认税费',
            metadata: [{ label: '已知小计', value: '840.00 元' }, { label: '原文', value: '甲商家报价原文' }],
          }, {
            key: 'source-1-0', title: '一次性纸杯 200ml', summary: '待核对实买价格',
            metadata: [
              { label: '来源', value: '本地商品库' }, { label: '目录参考价', value: '4.40 元' },
              { label: '查询时间', value: '2026-11-24T07:27:33Z' },
            ],
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
    const report = within(await screen.findByRole('region', { name: '采购结果' }))
    const needs = within(report.getByRole('table', { name: '采购需求' }))
    const quotes = within(report.getByRole('table', { name: '商家报价' }))
    const candidates = within(report.getByRole('table', { name: '商品候选' }))
    expect(needs.getByRole('rowheader', { name: '纸杯' })).toBeVisible()
    expect(needs.getByText('待明确')).toBeVisible()
    expect(quotes.getByText('确认运费')).toBeVisible()
    expect(quotes.getByText('840.00 元')).toBeVisible()
    expect(candidates.getByText('4.40 元')).toBeVisible()
    expect(candidates.getByText('本地商品库')).toBeVisible()
    expect(candidates.getByText(/15:27/)).toBeVisible()
    expect(report.queryByText('查看明细')).not.toBeInTheDocument()
    const quoteDetail = quotes.getByText('报价原文与条款').closest('details')
    expect(quoteDetail).toBeInstanceOf(HTMLDetailsElement)
    expect(quotes.getByText('甲商家报价原文')).not.toBeVisible()
    changeDisclosure(quoteDetail as HTMLDetailsElement, true)
    expect(quotes.getByText('甲商家报价原文')).toBeVisible()
    expect(quotes.getByRole('list', { name: '待核对项' })).toHaveTextContent('确认税费')
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
      onEvent(event(4, 'message.completed', { content: '还需要你补充以下信息：\n\n1. 你准备哪天出发和返程？' }))
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
    expect(forecast.getByRole('list', { name: '天气提醒' })).toHaveTextContent('建议带伞')
    expect(forecast.getByRole('link', { name: 'Open-Meteo' })).toBeInTheDocument()
    expect(screen.getAllByText('你准备哪天出发和返程？')).toHaveLength(1)
    expect(screen.queryByRole('region', { name: '出差方案比较' })).toBeNull()
  })

  it('uses the latest travel comparison and waits for the user to send the selection', async () => {
    const specialist = { agent: 'travel', agent_run_id: 'trip' }
    const bookingUrl = 'https://example.com/offer?ref=original&ticket=1'
    const routeUrl = 'https://api.map.baidu.com/direction?origin=latlng:30.77,114.22&destination=latlng:30.5,114.41&coord_type=bd09ll'
    const queriedAt = '2026-11-23T08:00:00+08:00'
    const data = {
      request: {
        origin: '上海', destination: '武汉', depart_on: '2026-12-01', return_on: '2026-12-03',
        venue: '武汉国际博览中心', arrive_by: '15:00', budget_cny: 3500, rooms: 1, travelers: 2, buffer_minutes: 30,
      },
      revision: 2,
      plans: [{
        id: 'plan_hash', label: '高铁与会场附近住宿', transport: 'train-1', return_transport: 'flight-1', hotel: 'hotel-1',
        known_cost: '1200.00', currency: 'CNY', complete_price: false, hotel_commute_minutes: 22,
        cost_items: {
          outbound: { status: 'partial', amount: '1200.00', currency: 'CNY' },
          return: { status: 'price_unconfirmed' },
          hotel: { status: 'price_unconfirmed' },
          ground: { status: 'price_unconfirmed' },
        },
        arrival_commute_minutes: 35, arrival_status: 'met',
        missing: ['返程价格未返回', '到站后前往会场的接驳时间', '住宿房型'],
        warnings: ['房价按每晚显示，尚未取得含税总价'],
      }],
      offers: [
        {
          id: 'train-1', kind: 'transport', mode: 'rail', name: 'G123 次列车', source: 'flyai', from_stop: '上海虹桥',
          to_stop: '武汉站', departs_at: '2026-12-01T08:15:00+08:00', arrives_at: '2026-12-01T12:20:00+08:00',
          amount: '600.00', currency: 'CNY', price_basis: 'person', booking_url: bookingUrl, queried_at: queriedAt,
        },
        {
          id: 'flight-1', kind: 'transport', mode: 'flight', name: '返程航班', source: 'flyai',
          departs_local: '2026-12-03 08:15:00', arrives_local: '2026-12-03 09:40:00', amount: '260',
        },
        {
          id: 'hotel-1', kind: 'hotel', name: '会场附近酒店', source: 'flyai', booking_url: 'javascript:alert(1)',
          amount: '94', currency: 'CNY',
        },
        {
          id: 'flight-2', kind: 'transport', direction: 'outbound', mode: 'flight', name: '备选航班', source: 'flyai',
          from_stop: '浦东国际机场', to_stop: '天河国际机场', departs_local: '2026-12-01 22:15:00',
          arrives_local: '2026-12-02 00:10:00', amount: '250', booking_url: bookingUrl,
        },
      ],
      issues: ['跨平台报价尚未核对'],
      providers: [{ source: 'ctrip', status: 'unconfigured', message: '未配置授权' }],
      routes: {
        'train-1': { source: 'baidu', status: 'ok', origin: '武汉站', destination: '武汉国际博览中心',
          source_url: routeUrl, items: [{ duration_minutes: 35, distance_meters: 26800 }] },
        'hotel-1': { source: 'baidu', status: 'needs_selection', origin: '会场附近酒店',
          message: '请确认出发地点的完整地址。' },
      },
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
    const preview = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(null, { status: 502 }))
    try {
      fireEvent.click(result.getByRole('tab', { name: '地图与路线' }))
      fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '我先看一下地图' } })
      expect(result.getByRole('tab', { name: '地图与路线' })).toHaveAttribute('aria-selected', 'true')
      const routes = within(result.getByRole('tabpanel', { name: '地图与路线' }))
      expect(routes.getByText('35 分钟')).toBeVisible()
      expect(routes.getByRole('link', { name: '在百度地图查看路线' })).toHaveAttribute('href', routeUrl)
      expect(await routes.findByText('地图暂时无法加载，可打开百度地图查看。')).toBeVisible()
      expect(preview).toHaveBeenCalledWith('/api/v1/workspace/travel/map-preview', expect.objectContaining({
        method: 'POST', body: JSON.stringify({ origin: [114.22, 30.77], destination: [114.41, 30.5], path: [] }),
      }))
      fireEvent.mouseDown(routes.getByRole('combobox', { name: '接驳路线' }))
      fireEvent.click(await screen.findByRole('option', { name: /会场附近酒店.*地址待确认/ }))
      expect(routes.getByText('请确认出发地点的完整地址。')).toBeVisible()
    } finally {
      preview.mockRestore()
    }
    fireEvent.click(result.getByRole('tab', { name: '天气参考' }))
    const forecast = within(await result.findByRole('tabpanel', { name: '天气参考' }))
    expect(forecast.getByText('65% 降水概率', { exact: false })).toBeVisible()
    expect(forecast.getByRole('list', { name: '天气提醒' })).toHaveTextContent('带外套')
    const comparison = result.getByText('候选与来源').closest('[role="tabpanel"]')!
    expect(comparison).not.toBeNull()
    expect(comparison).not.toBeVisible()
    expect(forecast.queryByText('候选与来源')).toBeNull()
    fireEvent.click(result.getByRole('tab', { name: '费用明细' }))
    const fees = within(result.getByRole('tabpanel', { name: '费用明细' }))
    expect(fees.getByText('可核算小计')).toBeVisible()
    expect(fees.getAllByText('1,200 元', { exact: true })).toHaveLength(2)
    expect(fees.getByText('参考价 260')).toBeVisible()
    expect(fees.getByText('参考价 94 元')).toBeVisible()
    expect(fees.getByText('地面接驳')).toBeVisible()
    expect(fees.getByText('待报价')).toBeVisible()
    expect(fees.queryByText(/接驳时间|住宿房型/)).toBeNull()
    fireEvent.click(result.getByRole('tab', { name: '方案对比' }))
    expect(result.getByText('已知费用小计')).toBeVisible()
    fireEvent.click(result.getByText('方案明细'))
    expect(result.getByText('从酒店出发约 22 分钟')).toBeInTheDocument()
    expect(result.getByText('抵达后接驳约 35 分钟')).toBeInTheDocument()
    expect(result.getByText('出发 2026-12-03 08:15 +08:00')).toBeVisible()
    expect(result.getByText('到达 2026-12-03 09:40 +08:00')).toBeVisible()
    fireEvent.click(result.getByText('候选与来源'))
    const candidates = within(result.getByRole('region', { name: '去程交通' }))
    expect(candidates.getByText('出发 · 12/1')).toBeVisible()
    expect(candidates.getByText('到达 · 12/2')).toBeVisible()
    expect(candidates.getByText('00:10')).toBeVisible()
    expect(candidates.getByRole('presentation')).toHaveAttribute('src', expect.stringContaining('workbench-travel-flight.webp'))
    expect(candidates.getByRole('link', { name: /备选航班/ })).toHaveAttribute('href', bookingUrl)
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
    getAgentRun.mockResolvedValue({id:'run_1',status:'FAILED',checkpoint:{id:'checkpoint-1'}})
    const {container} = render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', {name:'给业务助理发送消息'}), {target:{value:'给页面提建议'}})
    fireEvent.click(screen.getByRole('button', {name:'发送消息'}))
    expect(await screen.findByText('已保留本次取得的数据，见下方。')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', {name:'恢复任务'}))
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
    expect(screen.getByRole('alert')).toHaveTextContent('资料暂时不可用')
    expect(screen.queryByRole('button', { name: '恢复任务' })).toBeNull()
  })

  it('keeps an early failure across reloads and only offers recovery with a checkpoint', async () => {
    getAgentRun.mockResolvedValue({ id: 'run_1', status: 'FAILED', checkpoint: null, error_message: '无法读取资料' })
    streamAgentEvents.mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      publish(event(1, 'run.started', {}))
      publish(event(2, 'run.failed', { message: '无法读取资料' }))
      return { lastEventId: 2, terminal: true }
    })
    const first = render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value: '核对资料' } })
    fireEvent.click(screen.getByLabelText('发送消息'))
    expect(await screen.findByRole('alert')).toHaveTextContent('无法读取资料')
    first.unmount()
    render(<ChatPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('无法读取资料')
    expect(screen.queryByRole('button', { name: '恢复任务' })).toBeNull()
    expect(createAgentRun).toHaveBeenCalledTimes(1)
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
    expect(list[0]).toHaveTextContent(/^医药业务已完成$/)
    expect(list[1]).toHaveTextContent(/^SEO \/ GEO已完成$/)
    const drawer = await openExecutionDrawer()
    changeDisclosure(drawer.getByText('工具调用详情').closest('details')!, true)
    const medicalDetails = await drawer.findByRole('region', { name: '医药业务' })
    const seoDetails = drawer.getByRole('region', { name: 'SEO / GEO' })
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
    expect(screen.getByLabelText('停止生成')).toBeDisabled()
    expect(screen.queryByRole('region', { name: '本次结果' })).not.toBeInTheDocument()
  })

  it('asks before replacing a typed home draft with a starter', () => {
    render(<ChatPage />)
    const composer = screen.getByLabelText('给业务助理发送消息')
    fireEvent.change(composer, { target: { value: '我还没有写完的问题' } })
    fireEvent.click(screen.getByRole('button', { name: /页面检查/ }))
    expect(composer).toHaveValue('我还没有写完的问题')
    fireEvent.click(screen.getByRole('button', { name: '保留草稿' }))
    expect(composer).toHaveValue('我还没有写完的问题')
    fireEvent.click(screen.getByRole('button', { name: /页面检查/ }))
    fireEvent.click(screen.getByRole('button', { name: '替换草稿' }))
    expect((composer as HTMLTextAreaElement).value).toContain('https://breadtopia.com/')
    expect(createAgentRun).not.toHaveBeenCalled()
  })

  async function openHistoryConversation(title: string) {
    act(() => {
      window.history.replaceState(null, '', '/#/history')
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(title) }))
    return screen.findByRole('textbox', { name: '给业务助理发送消息' })
  }

  it.each([false, true])('keeps a delayed creation with its conversation (finishes in background: %s)', async (finishesInBackground) => {
    let accept!: (receipt: CreateRunResponse) => void
    createAgentRun.mockImplementationOnce(() => new Promise(resolve => { accept = resolve }))
    let publish!: (item: AgentEvent) => void
    let finish!: () => void
    streamAgentEvents.mockImplementationOnce((_url, onEvent: typeof publish) => new Promise(resolve => {
      publish = onEvent
      finish = () => resolve({ lastEventId: 2, terminal: true })
    }))
    const send = (text: string) => {
      fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value: text } })
      fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    }
    render(<ChatPage />)
    send('正在创建的第一个任务')
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }))
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value: '另一会话的草稿' } })

    const acceptFirst = async () => {
      await act(async () => accept({ run_id: 'run_1', status: 'pending', events_url: '/first-events', cancel_url: '/first-cancel' }))
    }
    if (finishesInBackground) {
      await acceptFirst()
      expect(screen.getByLabelText('给业务助理发送消息')).toHaveValue('另一会话的草稿')
      expect(streamAgentEvents).not.toHaveBeenCalled()
    }

    const composer = await openHistoryConversation('正在创建的第一个任务')
    expect(composer).toBeDisabled()
    if (!finishesInBackground) {
      expect(screen.getByRole('button', { name: '停止生成' })).toBeDisabled()
      expect(screen.queryByRole('button', { name: '发送消息' })).not.toBeInTheDocument()
      await acceptFirst()
    }
    await waitFor(() => expect(screen.getByRole('button', { name: '停止生成' })).toBeEnabled())
    expect(createAgentRun).toHaveBeenCalledTimes(1)
    expect(streamAgentEvents).toHaveBeenCalledTimes(1)
    expect(streamAgentEvents.mock.calls[0][0]).toBe('/first-events')
    await act(async () => {
      publish(event(1, 'message.completed', { content: '第一个任务的回复' }))
      publish(event(2, 'run.completed', {}))
      finish()
    })
    expect(await screen.findByText('第一个任务的回复')).toBeInTheDocument()
    expect(composer).toBeEnabled()
  })

  it('keeps an attachment draft after a background rejection and replaces the failed attempt on retry', async () => {
    const document = {
      id: 'a'.repeat(32), name: '待核对资料.md', in_library: false,
      size_bytes: 20, text_chars: 10, uploaded_at: '2026-11-24T00:00:00Z',
    }
    const conversation: Conversation = {
      id: 'with-document', title: '新会话', updatedAt: document.uploaded_at,
      messages: [], draftDocuments: [document],
    }
    window.localStorage.setItem('agent-workspace-conversations-v2', JSON.stringify([conversation]))
    let reject!: (error: Error) => void
    createAgentRun.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    const send = () => fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value: '核对附件中的表格' } })
    send()
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }))
    const otherComposer = screen.getByLabelText('给业务助理发送消息')
    fireEvent.change(otherComposer, { target: { value: '另一会话的草稿' } })
    await act(async () => reject(new Error('选中资料暂不可用。')))
    expect(otherComposer).toHaveValue('另一会话的草稿')
    expect(screen.queryByText('选中资料暂不可用。')).not.toBeInTheDocument()

    const composer = await openHistoryConversation('核对附件中的表格')
    expect(composer).toHaveValue('核对附件中的表格')
    expect(screen.getByRole('alert')).toHaveTextContent('选中资料暂不可用。')
    expect(screen.getByRole('list', { name: '待发送附件' })).toHaveTextContent(document.name)
    send()
    expect(await screen.findByText('分析完成')).toBeInTheDocument()
    expect(createAgentRun).toHaveBeenCalledTimes(2)
    expect(createAgentRun.mock.calls[1]).toEqual([[
      expect.objectContaining({ content: '核对附件中的表格', documents: [document] }),
    ], 'with-document'])
    const transcript = within(screen.getByRole('region', { name: '对话消息' }))
    expect(transcript.getAllByText('核对附件中的表格')).toHaveLength(1)
  })

  it.each(['waiting_for_input', 'completed', 'failed'] as const)(
    'opens the older %s receipt without changing the live run or its controls', async (status) => {
      const createdAt = '2026-11-24T00:00:00Z'
      const conversation: Conversation = {
        id: 'shared', title: '早期任务', updatedAt: createdAt,
        messages: [
          { id: 'old-question', role: 'user', content: '早期问题', createdAt },
          { id: 'old-reply', role: 'assistant', content: '这是旧任务的结果。', createdAt, runId: 'old', runStatus: status,
            inputRequest: status === 'waiting_for_input' ? { requestId: 'old-input', questions: ['旧任务的规格是什么？'], allowUseAvailable: true } : undefined,
            toolResults: [{ id: 'old-tool', name: 'medical_exam_syllabus', status: 'completed', render_hint: 'cards',
              result: { view_type: 'cards', items: [{ key: 'old-citation', title: '旧任务的考纲', url: 'https://old.example/syllabus' }] } }] },
          { id: 'live-question', role: 'user', content: '当前任务', createdAt },
        ],
        activeRun: { runId: 'live', afterSequence: 0, replyId: 'live-reply', eventsUrl: '/live/events', cancelUrl: '/live/cancel' },
      }
      window.localStorage.setItem('agent-workspace-conversations-v2', JSON.stringify([conversation]))
      window.history.replaceState(null, '', '/#/tasks')
      listWorkspaceTasks.mockResolvedValue([{
        id: 'old-task', conversation_id: 'shared', current_run_id: 'old', title: '早期任务', status: status.toUpperCase(),
        can_resume: status === 'failed', partial: false, created_at: createdAt, updated_at: createdAt,
      }])
      getAgentRun.mockImplementationOnce(async () => ({ id: 'old', status: status.toUpperCase(), checkpoint: { attempt: 1 } }))
      let publish!: (item: AgentEvent) => void
      let liveSignal!: AbortSignal
      streamAgentEvents.mockImplementationOnce((_url, onEvent, options) => new Promise(() => {
        publish = onEvent
        liveSignal = options.signal
      }))
      cancelAgentRun.mockResolvedValue({ run_id: 'live', status: 'cancelled' })

      render(<ChatPage />)
      await waitFor(() => expect(streamAgentEvents).toHaveBeenCalledTimes(1))
      fireEvent.click(await screen.findByRole('button', { name: /早期任务/ }))
      expect(screen.getByRole('heading', { name: '早期任务' })).toBeInTheDocument()
      expect((await screen.findByText('这是旧任务的结果。')).closest('article')).toHaveAttribute('aria-current', 'true')
      if (status === 'waiting_for_input') expect(screen.getByText('旧任务的规格是什么？')).toBeVisible()
      expect(screen.queryByRole('textbox', { name: '给业务助理发送消息' })).not.toBeInTheDocument()
      for (const name of ['停止生成', '恢复任务', '发送消息', '先用现有信息生成']) {
        expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
      }
      await act(async () => publish({ ...event(1, 'message.delta', { delta: '当前任务仍在执行' }), run_id: 'live' }))
      expect(liveSignal.aborted).toBe(false)
      expect(screen.getByRole('heading', { name: '早期任务' })).toBeInTheDocument()
      const drawer = await openExecutionDrawer()
      fireEvent.click(drawer.getByRole('tab', { name: '引用来源 1' }))
      expect(drawer.getByRole('link', { name: '旧任务的考纲' })).toHaveAttribute('href', 'https://old.example/syllabus')
      fireEvent.click(drawer.getByRole('button', { name: 'Close' }))
      expect(cancelAgentRun).not.toHaveBeenCalled()
      expect(resumeAgentRun).not.toHaveBeenCalled()
      expect(submitAgentInput).not.toHaveBeenCalled()
      expect(createAgentRun).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: '返回当前任务' }))
      expect(screen.getByRole('heading', { name: '当前任务' })).toBeInTheDocument()
      expect(await screen.findByText('当前任务仍在执行')).toBeVisible()
      fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
      await waitFor(() => expect(cancelAgentRun).toHaveBeenCalledWith('/live/cancel'))
      expect(streamAgentEvents).toHaveBeenCalledTimes(1)
    },
  )

  it('hydrates an older task receipt without overwriting the active run or the saved transcript', async () => {
    const createdAt = '2026-11-24T00:00:00Z'
    const conversation: Conversation = {
      id: 'shared', title: '旧任务问题', updatedAt: createdAt,
      messages: [
        { id: 'old-question', role: 'user', content: '旧任务问题', createdAt },
        { id: 'live-question', role: 'user', content: '当前任务', createdAt },
      ],
      activeRun: { runId: 'live', afterSequence: 0, replyId: 'live-reply', eventsUrl: '/live/events', cancelUrl: '/live/cancel' },
    }
    window.localStorage.setItem('agent-workspace-conversations-v2', JSON.stringify([conversation]))
    window.history.replaceState(null, '', '/#/tasks')
    listWorkspaceTasks.mockResolvedValue([{
      id: 'old-task', conversation_id: 'shared', current_run_id: 'old', title: '旧任务问题', status: 'COMPLETED',
      can_resume: false, partial: false, created_at: createdAt, updated_at: createdAt,
    }])
    getAgentRun.mockImplementationOnce(async () => ({ id: 'old', status: 'COMPLETED', events: [
      event(1, 'message.completed', { content: '从旧任务记录找回的结论。' }), event(2, 'run.completed', {}),
    ] }))
    streamAgentEvents.mockImplementationOnce(() => new Promise(() => {}))
    render(<ChatPage />)
    fireEvent.click(await screen.findByRole('button', { name: /旧任务问题/ }))
    expect((await screen.findByText('从旧任务记录找回的结论。')).closest('article')).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('heading', { name: '旧任务问题' })).toBeVisible()
    expect(JSON.parse(localStorage.getItem('agent-workspace-conversations-v2') ?? '[]')).toEqual([conversation])
    fireEvent.click(screen.getByRole('button', { name: '返回当前任务' }))
    expect(screen.queryByText('从旧任务记录找回的结论。')).not.toBeInTheDocument()
    expect(streamAgentEvents).toHaveBeenCalledTimes(1)
  })

  it('searches saved answers and displayed result fields and opens the exact matching turn', async () => {
    const createdAt = '2026-11-24T00:00:00Z'
    const conversation: Conversation = {
      id: 'saved', title: '业务会话', updatedAt: createdAt,
      messages: [
        { id: 'question', role: 'user', content: '查看本次检索结果', createdAt },
        {
          id: 'match', role: 'assistant', content: '已核查华中考区。<｜DSML｜tool_calls>隐藏控制文本</｜DSML｜tool_calls>',
          runId: 'first', runStatus: 'completed', createdAt,
          toolResults: [{
            id: 'report', name: 'medical_exam_syllabus', status: 'completed', render_hint: 'cards',
            arguments: { secret: '内部参数标记' },
            result: { success: true, metadata: { secret: '内部元数据标记' }, data: {
              view_type: 'cards', raw: { data: '内部证据标记' },
              items: [{ key: 'result', title: '考纲变更', summary: '规定必须核对最新版公开考试公告。', internal: '内部条目标记' }],
            } },
          }],
        },
        { id: 'question-2', role: 'user', content: '继续核查', createdAt },
        { id: 'reply-2', role: 'assistant', content: '这是更新的回复。', runId: 'second', runStatus: 'completed', createdAt },
      ],
    }
    localStorage.setItem('agent-workspace-conversations-v2', JSON.stringify([conversation]))
    window.history.replaceState(null, '', '/#/history')
    render(<ChatPage />)
    const search = screen.getByRole('textbox', { name: '搜索会话' })
    const searchFor = (value: string) => fireEvent.change(search, { target: { value } })
    for (const term of ['隐藏控制文本', '内部参数标记', '内部元数据标记', '内部证据标记', '内部条目标记']) {
      searchFor(term)
      expect(screen.getByText('没有匹配的会话。')).toBeInTheDocument()
    }
    searchFor('华中考区')
    expect(screen.getByText('已核查华中考区。')).toBeInTheDocument()
    searchFor('最新版公开考试公告')
    expect(listWorkspaceTasks.mock.calls.every(([ids]) => JSON.stringify(ids) === '["saved"]')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /业务会话.*规定必须核对最新版公开考试公告/ }))
    const matchingAnswer = await screen.findByText('规定必须核对最新版公开考试公告。')
    const resultMessage = matchingAnswer.closest('article.chat-message')
    expect(resultMessage).toHaveAttribute('aria-current', 'true')
    expect(resultMessage).toHaveFocus()
    expect(screen.getByText('这是更新的回复。').closest('article')).not.toHaveAttribute('aria-current')
    fireEvent.click(screen.getByRole('button', { name: '返回当前对话' }))
    expect(screen.getByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
  })

  it('uses the same safe item citations in executed cards and the deduplicated source list', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      publish(event(1, 'tool.completed', {
        tool_call_id: 'source-tool', name: 'medical_exam_syllabus', render_hint: 'cards',
        result: { success: true, data: {
          view_type: 'cards', sources: ['https://public.example/syllabus'], raw: { url: 'https://private.example/raw' },
          items: [
            { key: 'one', title: '公开考纲公告', url: 'https://PUBLIC.example:443/syllabus' },
            { key: 'two', title: '官方报名公告', source_url: 'https://public.example/registration' },
            { key: 'repeat', title: '重复的考纲', url: 'https://public.example/syllabus' },
            { key: 'unsafe', title: '不安全链接', url: 'javascript:alert(1)' },
            { key: 'credentials', title: '含凭据链接', url: 'https://user:secret@private.example/' },
          ],
        } },
      }))
      publish(event(2, 'message.completed', { content: '已核实两条来源。' }))
      publish(event(3, 'run.completed', {}))
    })
    render(<ChatPage />)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), { target: { value: '核查考纲和报名公告' } })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const cards = within(await screen.findByTestId('card-result'))
    const syllabus = cards.getByRole('link', { name: '公开考纲公告' })
    const registration = cards.getByRole('link', { name: '官方报名公告' })
    expect(cards.getAllByRole('link')).toHaveLength(3)
    const drawer = await openExecutionDrawer()
    fireEvent.click(drawer.getByRole('tab', { name: '引用来源 2' }))
    expect(drawer.getAllByRole('link')).toEqual([
      drawer.getByRole('link', { name: '公开考纲公告' }), drawer.getByRole('link', { name: '官方报名公告' }),
    ])
    expect(drawer.getByRole('link', { name: '公开考纲公告' })).toHaveAttribute('href', syllabus.getAttribute('href'))
    expect(drawer.getByRole('link', { name: '官方报名公告' })).toHaveAttribute('href', registration.getAttribute('href'))
  })

  it('starts a separate task while waiting and keeps the original answer draft tied to the same run', async () => {
    streamAgentEvents.mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      publish(event(1, 'message.completed', { content: '请选择尺寸。' }))
      publish(event(2, 'run.waiting_for_input', { request_id: 'size', questions: ['需要什么尺寸？'] }))
      return { lastEventId: 2, terminal: true }
    }).mockImplementationOnce(async (_url, publish: (item: AgentEvent) => void) => {
      publish({ ...event(1, 'message.completed', { content: '这是独立任务的回复。' }), run_id: 'independent' })
      publish({ ...event(2, 'run.completed', {}), run_id: 'independent' })
      return { lastEventId: 2, terminal: true }
    })
    const send = () => fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    const change = (value: string) => fireEvent.change(screen.getByLabelText('给业务助理发送消息'), { target: { value } })
    render(<ChatPage />)
    change('原始采购任务')
    send()
    const newTask = await screen.findByRole('button', { name: '开始新任务' })
    change('大号')
    fireEvent.click(newTask)
    expect(screen.getByLabelText('给业务助理发送消息')).toHaveValue('')
    createAgentRun.mockResolvedValueOnce({ run_id: 'independent', status: 'pending', events_url: '/independent/events', cancel_url: '/independent/cancel' })
    change('一项独立任务')
    send()
    expect(await screen.findByText('这是独立任务的回复。')).toBeInTheDocument()
    expect(createAgentRun).toHaveBeenCalledTimes(2)
    expect(createAgentRun.mock.calls[1][1]).not.toBe(createAgentRun.mock.calls[0][1])
    expect(createAgentRun.mock.calls[1][0]).toEqual([expect.objectContaining({ content: '一项独立任务' })])
    expect(submitAgentInput).not.toHaveBeenCalled()

    const composer = await openHistoryConversation('原始采购任务')
    expect(composer).toHaveValue('大号')
    expect(screen.getByText('需要什么尺寸？')).toBeInTheDocument()
    submitAgentInput.mockResolvedValueOnce({ run_id: 'run_1', status: 'recovering', events_url: '/events', cancel_url: '/cancel', after_sequence: 3 })
    send()
    await waitFor(() => expect(submitAgentInput).toHaveBeenCalledWith('run_1', 'size', '大号', 'answer'))
    expect(createAgentRun).toHaveBeenCalledTimes(2)
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
    expect(within(reply).queryByText('工具调用详情')).not.toBeInTheDocument()
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


  it('shows tool calls in the execution drawer and opens returned data on demand', async () => {
    render(<ChatPage />)
    fireEvent.change(screen.getByLabelText('给业务助理发送消息'), {
      target: { value: '分析自然流量下降' },
    })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await screen.findByText('分析完成')
    expect(screen.queryByLabelText('Agent 执行详情')).not.toBeInTheDocument()
    const drawer = await openExecutionDrawer()
    const details = drawer.getByText('工具调用详情').closest('details')!
    expect(details).toHaveAttribute('open')
    expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument()

    const call = (await within(details).findByText('关键词明细')).closest<HTMLDetailsElement>('details.chat-run-tool')!
    expect(call).not.toHaveAttribute('open')
    changeDisclosure(call, true)
    const parameters = within(await within(call).findByRole('region', { name: '调用参数' }))
    expect(parameters.getByTestId('raw-tool-result')).toHaveTextContent('"domain": "example.com"')
    expect(within(details).getByText('2.5 秒')).toBeInTheDocument()
    expect(within(details).queryByText('run.started')).not.toBeInTheDocument()
    expect(within(details).queryByText('tool.completed')).not.toBeInTheDocument()

    const result = within(details).getByText('返回数据').closest('details')!
    expect(result).not.toHaveAttribute('open')
    changeDisclosure(result, true)
    expect(await within(result).findByTestId('raw-tool-result')).toHaveTextContent('seo agent')
    expect(screen.getAllByRole('table')).toHaveLength(1)

    changeDisclosure(details, false)
    await waitFor(() => expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument())
  })

})
