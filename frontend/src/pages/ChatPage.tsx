import {
  BulbOutlined,
  CloseOutlined,
  DownOutlined,
  MenuOutlined,
  PlusOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { Alert, Button, Empty, Input, Tag, Tooltip } from 'antd'
import { useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { InlineToolResults } from '../components/InlineToolResults'
import { MarkdownMessage } from '../components/MarkdownMessage'
import { ToolResultView } from '../components/ToolResultView'
import {
  cancelAgentRun,
  createAgentRun,
  getAgentRun,
  resumeAgentRun,
  submitAgentInput,
  streamAgentEvents,
} from '../services/agentApi'
import { loadProductConfig } from '../services/productApi'
import { initialRunState, reduceAgentEvent } from '../state/agentState'
import { createConversation, loadConversations, saveConversations } from '../state/conversations'
import type {
  AgentEvent,
  ChatMessage,
  Conversation,
  ToolRun,
} from '../types/agent'

const { TextArea } = Input

const starters = [
  {
    title: '给首页做个体检',
    detail: '从标题、H1 和页面基础信息开始。',
    icon: SearchOutlined,
    tone: 'blue',
    prompt: '请给本站首页做一次 SEO 基础体检，读取页面源 HTML，不需要性能检查。',
  },
  {
    title: '发现内容新机会',
    detail: '用关键词和搜索结果，找找新方向。',
    icon: BulbOutlined,
    tone: 'amber',
    prompt: '我想为本站找关键词机会，请先帮我明确需要提供哪些业务信息，再进行查询。',
  },
  {
    title: '给页面拟份建议',
    detail: '根据页面内容，拟定标题和分享信息。',
    icon: SafetyCertificateOutlined,
    tone: 'mint',
    prompt: '请根据本站首页现在的内容，给我一份标题、描述、关键词、分享信息和结构化数据建议；缺的信息告诉我，只提建议，不修改网站。',
  },
]

const stageLabels: Record<string, string> = {
  idle: '等待输入',
  waiting: '等待中',
  calling_model: '正在调用模型',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已停止',
  recovering: '恢复中',
  summarizing_budget: '正在基于现有证据总结',
}

const statusLabels: Record<string, string> = {
  waiting_for_input: '等待你补充',
  idle: '空闲',
  pending: '准备中',
  running: '生成中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
  recovering: '恢复中',
  summarizing_budget: '正在基于现有证据总结',
}

const persistableToolResults = (tools: ToolRun[]): ToolRun[] => tools.map((tool) => ({
  id: tool.id,
  name: tool.name,
  status: tool.status,
  render_hint: tool.render_hint,
  error: tool.error,
  result: removeRawEvidence(tool.result),
}))

const removeRawEvidence = (value: unknown): unknown => {
  if (!isRecord(value)) return value
  if (typeof value.success === 'boolean' && isRecord(value.data)) {
    const presentation = { ...value.data }
    delete presentation.raw
    return { ...value, data: presentation }
  }
  const presentation = { ...value }
  delete presentation.raw
  return presentation
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

interface ActiveRunConnection {
  runId: string
  eventsUrl: string
  cancelUrl: string
  conversationId: string
  lastEventId: number
}

interface ConnectionIssue {
  message: string
  canResume: boolean
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState(conversations[0].id)
  const [input, setInput] = useState('')
  const [run, dispatch] = useReducer(reduceAgentEvent, initialRunState)
  const [cancelUrl, setCancelUrl] = useState<string>()
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [expandedToolIds, setExpandedToolIds] = useState<Set<string>>(() => new Set())
  const [conversationsOpen, setConversationsOpen] = useState(false)
  const [activeTask, setActiveTask] = useState<{
    taskId?: string
    threadId?: string
  }>({})
  const [connectionIssue, setConnectionIssue] = useState<ConnectionIssue>()
  const [reconnecting, setReconnecting] = useState(false)
  const abortRef = useRef<AbortController | undefined>(undefined)
  const endRef = useRef<HTMLDivElement>(null)
  const runRef = useRef(initialRunState)
  const activeConnectionRef = useRef<ActiveRunConnection | undefined>(undefined)
  const active = conversations.find((item) => item.id === activeId) ?? conversations[0]
  const latestAssistant = [...active.messages].reverse().find((message) => message.role === 'assistant')
  const running = run.status === 'running' || run.status === 'pending' || run.status === 'recovering'
  const emptyConversation = active.messages.length === 0 && !run.assistantText && run.tools.length === 0
  const querying = run.tools.some((tool) => tool.status === 'running')
  const activity = querying
    ? { title: '正在读取实际数据', detail: '先核对来源，再给你判断。' }
    : run.tools.length > 0
      ? { title: '正在整理这次调查', detail: '把能确认的事实和暂时缺少的证据分开说明。' }
      : { title: '正在理解你的问题', detail: '先看清目标，再找需要的证据。' }

  useEffect(() => {
    saveConversations(conversations)
  }, [conversations])

  useEffect(() => {
    runRef.current = run
  }, [run])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [active.messages, run.assistantText, run.tools.length])

  const setActiveConversation = (updater: (conversation: Conversation) => Conversation) => {
    setConversations((items) => items.map((item) => (item.id === activeId ? updater(item) : item)))
  }

  const applyEvent = (event: AgentEvent) => {
    runRef.current = reduceAgentEvent(runRef.current, event)
    dispatch(event)
  }

  const resetRun = (status: 'idle' | 'pending') => {
    const event: AgentEvent = {
      event_id: crypto.randomUUID(),
      run_id: '',
      type: 'run.reset',
      sequence: 0,
      created_at: new Date().toISOString(),
      payload: { status },
    }
    runRef.current = reduceAgentEvent(runRef.current, event)
    dispatch(event)
  }

  const persistRunResult = (connection: ActiveRunConnection) => {
    const state = runRef.current
    if (!state.assistantText && state.tools.length === 0) return
    setConversations((items) => items.map((conversation) => {
      if (conversation.id !== connection.conversationId) return conversation
      const lastUser = conversation.messages.map((message) => message.role).lastIndexOf('user')
      const existing = conversation.messages.findIndex((message, index) => index > lastUser && message.runId === connection.runId)
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: state.assistantText || '已保留本次取得的数据，见下方。',
        createdAt: new Date().toISOString(),
        toolResults: persistableToolResults(state.tools),
        runId: connection.runId,
        inputRequest: state.humanInput,
      }
      return {
        ...conversation,
        messages: existing < 0 ? [...conversation.messages, assistantMessage]
          : conversation.messages.map((message, index) => index === existing
            ? { ...assistantMessage, id: message.id, createdAt: message.createdAt } : message),
        updatedAt: new Date().toISOString(),
      }
    }))
  }

  const consumeConnection = async (connection: ActiveRunConnection): Promise<boolean> => {
    const controller = new AbortController()
    abortRef.current = controller
    setConnectionIssue(undefined)
    try {
      const result = await streamAgentEvents(
        connection.eventsUrl,
        applyEvent,
        {
          signal: controller.signal,
          afterEventId: connection.lastEventId,
          onEventId: (eventId) => {
            connection.lastEventId = eventId
          },
          onReconnect: () => setReconnecting(true),
        },
      )
      if (result) connection.lastEventId = result.lastEventId
      persistRunResult(connection)
      return true
    } catch (cause) {
      if ((cause as Error).name === 'AbortError') return false
      let detail: Awaited<ReturnType<typeof getAgentRun>> | undefined
      try {
        detail = await getAgentRun(connection.runId)
      } catch {
        // The API may still be restarting. Keep the same Run for manual reconnect.
      }
      const status = String(detail?.status ?? detail?.task_status ?? '').toUpperCase()
      if (status === 'FAILED') {
        applyEvent({
          event_id: crypto.randomUUID(),
          run_id: connection.runId,
          type: 'run.failed',
          sequence: runRef.current.lastSequence + 1,
          created_at: new Date().toISOString(),
          payload: { message: detail?.error_message ?? 'Agent 执行失败' },
        })
        setConnectionIssue({ message: '任务执行失败，可从最近检查点恢复', canResume: true })
      } else if (status === 'CANCELLED') {
        applyEvent({
          event_id: crypto.randomUUID(),
          run_id: connection.runId,
          type: 'run.cancelled',
          sequence: runRef.current.lastSequence + 1,
          created_at: new Date().toISOString(),
          payload: { status: 'cancelled' },
        })
        setConnectionIssue({ message: '任务已经停止', canResume: false })
      } else {
        setConnectionIssue({
          message: '连接已中断，任务可能仍在后台执行',
          canResume: status === 'RECOVERY_BLOCKED',
        })
      }
      return false
    } finally {
      if (abortRef.current === controller) abortRef.current = undefined
      setReconnecting(false)
      if (['completed', 'failed', 'cancelled'].includes(runRef.current.status)) {
        setCancelUrl(undefined)
      }
    }
  }

  const reconnectActiveRun = async () => {
    const connection = activeConnectionRef.current
    if (!connection || reconnecting) return
    setReconnecting(true)
    setConnectionIssue(undefined)
    try {
      let detail: Awaited<ReturnType<typeof getAgentRun>> | undefined
      try {
        detail = await getAgentRun(connection.runId)
      } catch {
        // Reconnecting the event stream can still succeed after a short API restart.
      }
      const status = String(detail?.status ?? detail?.task_status ?? '').toUpperCase()
      if (status === 'FAILED' || status === 'RECOVERY_BLOCKED') {
        await resumeAgentRun(connection.runId)
        applyEvent({
          event_id: crypto.randomUUID(),
          run_id: connection.runId,
          type: 'run.resumed',
          sequence: runRef.current.lastSequence + 1,
          created_at: new Date().toISOString(),
          payload: { status: 'recovering' },
        })
      }
      await consumeConnection(connection)
    } catch (cause) {
      setConnectionIssue({
        message: (cause as Error).message || '重新连接失败',
        canResume: connectionIssue?.canResume ?? false,
      })
      setReconnecting(false)
    }
  }

  const newConversation = () => {
    const conversation = createConversation()
    setConversations((items) => [conversation, ...items])
    setActiveId(conversation.id)
    setConversationsOpen(false)
    setActiveTask({})
    setConnectionIssue(undefined)
    setExpandedToolIds(new Set())
    activeConnectionRef.current = undefined
    resetRun('idle')
  }

  const send = async (action: 'answer' | 'use_available' = 'answer') => {
    const content = action === 'use_available'
      ? '请先用现有事实生成草稿，未确认的内容保留待补充，结束本轮追问。'
      : input.trim()
    if (!content || running) return

    setInput('')
    setConnectionIssue(undefined)
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    }
    const history = [...active.messages, userMessage]
    setActiveConversation((conversation) => ({
      ...conversation,
      title: conversation.messages.length === 0 ? content.slice(0, 28) : conversation.title,
      messages: history,
      updatedAt: new Date().toISOString(),
    }))
    resetRun('pending')

    try {
      const productConfig = loadProductConfig()
      if (!productConfig?.siteId) throw new Error('站点配置不存在，请重新初始化工作区')
      const previous = latestAssistant
      const created = previous?.inputRequest && previous.runId
        ? await submitAgentInput(previous.runId, previous.inputRequest.requestId, content, action)
        : await createAgentRun(history, {
          siteId: productConfig.siteId,
          conversationId: active.id,
        })
      setActiveTask({ taskId: created.task_id, threadId: created.thread_id })
      setCancelUrl(created.cancel_url)
      const connection: ActiveRunConnection = {
        runId: created.run_id,
        eventsUrl: created.events_url,
        cancelUrl: created.cancel_url,
        conversationId: active.id,
        lastEventId: created.after_sequence ?? 0,
      }
      activeConnectionRef.current = connection
      await consumeConnection(connection)
    } catch (cause) {
      applyEvent({
        event_id: crypto.randomUUID(),
        run_id: '',
        type: 'run.failed',
        sequence: runRef.current.lastSequence + 1,
        created_at: new Date().toISOString(),
        payload: { message: (cause as Error).message },
      })
    }
  }

  const stop = async () => {
    try {
      if (cancelUrl) await cancelAgentRun(cancelUrl)
    } finally {
      abortRef.current?.abort()
      setConnectionIssue(undefined)
      applyEvent({
        event_id: crypto.randomUUID(),
        run_id: runRef.current.runId ?? '',
        type: 'run.cancelled',
        sequence: runRef.current.lastSequence + 1,
        created_at: new Date().toISOString(),
        payload: { status: 'cancelled' },
      })
    }
  }

  const statusColor = useMemo(
    () =>
      ({
        completed: 'success',
        failed: 'error',
        cancelled: 'default',
        running: 'processing',
        recovering: 'processing',
        pending: 'warning',
        waiting_for_input: 'warning',
        idle: 'default',
      })[run.status] as 'success' | 'error' | 'default' | 'processing' | 'warning',
    [run.status],
  )

  const selectConversation = (id: string) => {
    setActiveId(id)
    setConversationsOpen(false)
    setConnectionIssue(undefined)
    setExpandedToolIds(new Set())
    activeConnectionRef.current = undefined
    resetRun('idle')
  }

  const closePanels = () => {
    setInspectorOpen(false)
    setConversationsOpen(false)
  }

  return (
    <div className={`chat-workspace${inspectorOpen ? ' inspector-open' : ''}${conversationsOpen ? ' conversations-open' : ''}`}>
      <button className="chat-panel-backdrop" aria-label="关闭侧边面板" onClick={closePanels} />

      <aside className="chat-conversations" aria-label="会话列表">
        <div className="chat-sidebar-head">
          <div>
            <strong>会话</strong>
            <span>{conversations.length} 个对话</span>
          </div>
          <Tooltip title="新建会话">
            <Button aria-label="新建会话" type="text" shape="circle" icon={<PlusOutlined />} onClick={newConversation} />
          </Tooltip>
        </div>
        <div className="conversation-list product-conversations">
          {conversations.map((conversation) => (
            <button
              className={conversation.id === activeId ? 'conversation active' : 'conversation'}
              key={conversation.id}
              onClick={() => selectConversation(conversation.id)}
            >
              <span>{conversation.title}</span>
              <small>{conversation.messages.length} 条消息</small>
            </button>
          ))}
        </div>
        <div className="chat-sidebar-foot">
          <span className="online-dot" />
          <div><strong>SEO 优化专员</strong><small>已连接 · 可随时对话</small></div>
        </div>
      </aside>

      <section className={`chat-main${emptyConversation ? ' has-welcome' : ''}`}>
        <header className="chat-title-bar">
          <div className="chat-title-left">
            <Tooltip title="查看会话">
              <Button
                className="conversation-toggle"
                aria-label="打开会话列表"
                type="text"
                icon={<MenuOutlined />}
                onClick={() => setConversationsOpen(true)}
              />
            </Tooltip>
            <div>
              <strong>{active.title}</strong>
              <span>{stageLabels[run.stage] ?? run.stage}</span>
              {activeTask.taskId ? (
                <small className="agent-run-identity">
                  Task {activeTask.taskId.slice(-8)}
                  {activeTask.threadId ? ` · Thread ${activeTask.threadId.slice(-8)}` : ''}
                </small>
              ) : null}
            </div>
          </div>
          <div className="chat-title-actions">
            <Tag color={statusColor}>{statusLabels[run.status] ?? run.status}</Tag>
            <Button
              aria-label="执行详情"
              aria-pressed={inspectorOpen}
              icon={<ToolOutlined />}
              onClick={() => setInspectorOpen((value) => !value)}
            >
              执行详情
            </Button>
          </div>
        </header>

        <div className="chat-messages">
          {emptyConversation ? (
            <div className="chat-welcome">
              <div className="chat-welcome-mark" aria-hidden="true">
                <BulbOutlined />
                <span className="chat-welcome-spark">✦</span>
              </div>
              <div className="chat-welcome-eyebrow">你的 SEO 分析搭档</div>
              <h1>你来提问，<span>我来查证。</span></h1>
              <p>检查页面、研究关键词、核对改动。<br />每一步，都带着证据。</p>
              <div className="suggestions">
                {starters.map((starter) => {
                  const Icon = starter.icon
                  return (
                    <button key={starter.title} onClick={() => setInput(starter.prompt)}>
                      <span className="suggestion-top" aria-hidden="true">
                        <span className={`suggestion-icon ${starter.tone}`}><Icon /></span>
                        <RightOutlined className="suggestion-arrow" />
                      </span>
                      <strong>{starter.title}</strong>
                      <span className="suggestion-detail">{starter.detail}</span>
                    </button>
                  )
                })}
              </div>
              <small><SafetyCertificateOutlined aria-hidden="true" /> 先分析，不自动修改或发布。</small>
            </div>
          ) : null}

          {active.messages.map((item) => (
            <article className={`chat-message ${item.role}`} key={item.id}>
              <div className={`avatar${item.role === 'assistant' ? ' agent-avatar' : ''}`} aria-hidden="true">
                {item.role === 'user' ? '你' : <BulbOutlined />}
              </div>
              <div className="chat-message-content">
                {item.role === 'assistant' ? (
                  <>
                    <div className="assistant-name">SEO 优化专员 <span aria-hidden="true">✦</span></div>
                    <MarkdownMessage content={item.content} />
                    <InlineToolResults tools={item.toolResults} />
                  </>
                ) : item.content}
              </div>
            </article>
          ))}

          {running ? (
            <article className="chat-message assistant streaming">
              <div className="avatar agent-avatar" aria-hidden="true"><BulbOutlined /></div>
              <div className="chat-message-content">
                <div className="assistant-name">SEO 优化专员 <span aria-hidden="true">✦</span></div>
                {!run.assistantText || querying ? (
                  <div className="agent-activity" role="status" aria-label="分析进度" aria-live="polite">
                    <div className="agent-activity-title">
                      <strong>{activity.title}</strong>
                      <span className="activity-dots" aria-hidden="true"><i /><i /><i /></span>
                    </div>
                    <span>{activity.detail}</span>
                  </div>
                ) : null}
                {run.assistantText ? <MarkdownMessage content={run.assistantText} /> : null}
                <InlineToolResults tools={run.tools} />
                {run.assistantText ? <span className="cursor" /> : null}
              </div>
            </article>
          ) : null}
          <div ref={endRef} />
        </div>

        <div className="chat-composer-wrap">
          {connectionIssue ? (
            <Alert
              type="warning"
              showIcon
              title={connectionIssue.message}
              description="将继续使用同一个 Run 和已保存的检查点，不会重新执行已经成功的工具。"
              action={(
                <Button size="small" loading={reconnecting} onClick={() => void reconnectActiveRun()}>
                  {connectionIssue.canResume ? '恢复任务' : '重新连接'}
                </Button>
              )}
            />
          ) : null}
          {run.status === 'failed' && run.error && !connectionIssue ? (
            <Alert
              type="error"
              showIcon
              title={run.error}
              description={run.runId ? '可以从已保存的检查点直接生成已有证据结论，不会重跑成功工具。' : undefined}
              action={run.runId ? (
                <Button size="small" onClick={() => void reconnectActiveRun()}>
                  基于已有证据完成
                </Button>
              ) : undefined}
            />
          ) : null}
          <div className="chat-composer">
            <TextArea
              aria-label="给 SEO 优化专员发送消息"
              value={input}
              disabled={running}
              placeholder="想先看看网站的哪一部分？"
              autoSize={{ minRows: 1, maxRows: 6 }}
              onChange={(event) => setInput(event.target.value)}
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault()
                  void send()
                }
              }}
            />
            {running ? (
              <Tooltip title="停止生成">
                <Button aria-label="停止生成" danger shape="circle" icon={<StopOutlined />} onClick={() => void stop()} />
              </Tooltip>
            ) : (
              <Tooltip title="发送消息">
                <Button
                  aria-label="发送消息"
                  type="primary"
                  shape="circle"
                  icon={<SendOutlined />}
                  disabled={!input.trim()}
                  onClick={() => void send()}
                />
              </Tooltip>
            )}
          </div>
          <small>{latestAssistant?.inputRequest && !running
            ? '进度已保存，补充后继续本次分析。'
            : 'Enter 发送，Shift + Enter 换行 · 只读分析，不自动修改网站'}
            {latestAssistant?.inputRequest?.allowUseAvailable && !running ? (
              <Button className="human-input-continue" type="link" size="small" onClick={() => void send('use_available')}>
                先用现有信息生成
              </Button>
            ) : null}
          </small>
        </div>
      </section>

      <aside
        className="chat-inspector"
        aria-label="Agent 执行详情"
        aria-hidden={!inspectorOpen}
        inert={!inspectorOpen}
      >
        <div className="inspector-title">
          <div><strong>执行详情</strong><span>阶段、工具与事件</span></div>
          <Button aria-label="关闭执行详情" type="text" shape="circle" icon={<CloseOutlined />} onClick={() => setInspectorOpen(false)} />
        </div>

        <section className="inspector-section">
          <label>当前阶段</label>
          <div className="stage-card"><span className={running ? 'pulse' : 'dot'} />{stageLabels[run.stage] ?? run.stage}</div>
        </section>

        <section className="inspector-section">
          <label>工具调用</label>
          {run.tools.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未调用工具" />
          ) : run.tools.map((tool) => {
            const latencyText = tool.latencyMs != null
              ? tool.latencyMs >= 1000
                ? `${(tool.latencyMs / 1000).toFixed(1)}s`
                : `${Math.round(tool.latencyMs)}ms`
              : null
            const expanded = expandedToolIds.has(tool.id)
            const detailId = `tool-detail-${tool.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
            return (
              <article className="tool-card" key={tool.id}>
                <button
                  className="tool-card-toggle"
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={detailId}
                  aria-label={`${expanded ? '收起' : '展开'}工具详情 ${tool.name}`}
                  onClick={() => setExpandedToolIds((current) => {
                    const next = new Set(current)
                    if (next.has(tool.id)) next.delete(tool.id)
                    else next.add(tool.id)
                    return next
                  })}
                >
                  {expanded ? <DownOutlined /> : <RightOutlined />}
                  <strong>{tool.name}</strong>
                  <Tag>{tool.status}</Tag>
                  {latencyText ? <span className="tool-latency">{latencyText}</span> : null}
                  <span className="tool-toggle-label">{expanded ? '收起' : '展开'}</span>
                </button>
                {expanded ? (
                  <div className="tool-card-detail" id={detailId}>
                    {tool.arguments ? <pre>{JSON.stringify(tool.arguments, null, 2)}</pre> : null}
                    {tool.result ? <ToolResultView hint={tool.render_hint} data={tool.result} /> : null}
                  </div>
                ) : null}
              </article>
            )
          })}
        </section>

        <section className="inspector-section">
          <label>最近事件</label>
          {run.events.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无运行事件" /> : (
            <div className="event-list">
              {run.events.slice(-12).map((event) => <div key={event.event_id}><span>{event.sequence}</span><p>{event.type}</p></div>)}
            </div>
          )}
        </section>
      </aside>
    </div>
  )
}
