import {
  ApartmentOutlined,
  BookOutlined,
  BranchesOutlined,
  BulbOutlined,
  CalculatorOutlined,
  ExperimentOutlined,
  FormOutlined,
  MedicineBoxOutlined,
  MenuOutlined,
  PlusOutlined,
  ProfileOutlined,
  ReadOutlined,
  RightOutlined,
  SearchOutlined,
  SendOutlined,
  SolutionOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { Alert, Button, Input, Tag, Tooltip } from 'antd'
import { type ComponentRef, lazy, Suspense, useEffect, useEffectEvent, useMemo, useReducer, useRef, useState } from 'react'

import { AssistantRunStatus } from '../components/AssistantRunStatus'
import { ChatRunDetails } from '../components/ChatRunDetails'
import { InlineToolResults } from '../components/InlineToolResults'
import { TravelReceipt } from '../components/TravelReceipt'
import {
  cancelAgentRun,
  createAgentRun,
  getAgentRun,
  resumeAgentRun,
  submitAgentInput,
  streamAgentEvents,
} from '../services/agentApi'
import type { FollowupAction } from '../services/followupApi'
import { initialRunState, reduceAgentEvent } from '../state/agentState'
import {
  createConversation,
  loadActiveConversationId,
  loadConversations,
  saveActiveConversationId,
  saveConversations,
} from '../state/conversations'
import type {
  AgentEvent,
  ChatMessage,
  Conversation,
  SavedAgentRun,
  ToolRun,
} from '../types/agent'

const { TextArea } = Input
const SpecialistDirectory = lazy(() => import('../components/SpecialistDirectory'))
const FollowupPanel = lazy(() => import('../components/FollowupPanel'))
const MathPracticeMessage = lazy(() => import('../components/MathPracticeMessage'))
const MarkdownMessage = lazy(async () => {
  const module = await import('../components/MarkdownMessage')
  return { default: module.MarkdownMessage }
})
const replyLoading = <div className="agent-activity" role="status">正在加载回复…</div>
const mathPracticeToolName = 'tal_math_practice'
const travelReceiptTools = new Set(['travel_weather', 'travel_compare_plans', 'travel_save_plan'])

const starterGroups = [
  {
    title: '考研与医考',
    tone: 'study',
    examples: [
      {
        title: '考研招生对比',
        detail: '按专业代码对照两校目录',
        icon: ReadOutlined,
        prompt: '比较 2026 年武汉科技大学 105101 和湖北民族大学 105100 的初试科目，请给出各自的招生目录出处。',
      },
      {
        title: '医考报名',
        detail: '查湖北考区的技能免考条件',
        icon: BookOutlined,
        prompt: '我 2025 年实践技能考过了，但医学综合考试没过。2026 年在湖北报考医师资格，还要重新考技能和办理报名吗？',
      },
    ],
  },
  {
    title: '医药资料',
    tone: 'medicine',
    examples: [
      {
        title: '随访与复诊准备',
        detail: '补齐资料，核对并保存待办',
        icon: ProfileOutlined,
        prompt: '用合成资料演示一次复诊准备。帮我核对两次报告，整理下次要带的资料和已有安排。',
      },
      {
        title: '医保药品目录',
        detail: '核查通用名、剂型和分类',
        icon: MedicineBoxOutlined,
        prompt: '帮我核对国家医保药品目录当前生效版，二甲双胍有哪些常规剂型，分别是甲类还是乙类？',
      },
      {
        title: 'PubMed 文献检索',
        detail: '看公开题录与 PMID',
        icon: ExperimentOutlined,
        prompt: '在 PubMed 上找 2024 至 2025 年关于青少年睡眠时长的 3 篇文献，列出题录和 PMID。',
      },
    ],
  },
  {
    title: '网站与内容',
    tone: 'web',
    examples: [
      {
        title: '页面检查',
        detail: '核对标题是否概括正文',
        icon: SearchOutlined,
        prompt: '帮我检查 https://breadtopia.com/how-to-make-a-sourdough-starter/ 的标题和正文是否匹配，给我最优先的修改建议和依据。',
      },
      {
        title: '网页与考纲联查',
        detail: '由两位专员比对公告与页面',
        icon: BranchesOutlined,
        prompt: '请按医学人文修订通知核对 https://www.hbwsrc.cn/news/show-1830.html 中的适用考试类别。',
      },
    ],
  },
  {
    title: '学习与备考',
    tone: 'learning',
    examples: [
      {
        title: '数学陪练',
        detail: '解方程，逐题看解析',
        icon: CalculatorOutlined,
        prompt: '我想练 3 道一元一次方程题，请一次出一道，做完给我看解析。',
      },
      {
        title: '教资笔试',
        detail: '查报名时间和备考大纲',
        icon: FormOutlined,
        prompt: '查一下 2026 年下半年湖北初中数学教资笔试的报名时间、考试科目和官方备考大纲。',
      },
    ],
  },
  {
    title: '商旅行程',
    tone: 'travel',
    examples: [{
      title: '比较出差方案',
      detail: '上海到武汉，查交通、住宿与会场通勤',
      icon: SolutionOutlined,
      prompt: '下周二从上海到武汉出差，周四返回。两人住一间房，预算 3500 元，周二下午 3 点前到武汉国际博览中心，预留 30 分钟。帮我比较高铁、飞机和酒店。',
    }],
  },
]

function ExampleGroup({ group, onChoose }: {
  group: (typeof starterGroups)[number]
  onChoose: (prompt: string) => void
}) {
  return (
    <section className={`example-group example-${group.tone}`} aria-label={group.title}>
      <h2>{group.title}</h2>
      {group.examples.map(({ title, detail, icon: Icon, prompt }) => (
        <button type="button" key={title} onClick={() => onChoose(prompt)}>
          <span className="suggestion-icon"><Icon aria-hidden="true" /></span>
          <span className="suggestion-copy"><strong>{title}</strong><small>{detail}</small></span>
          <RightOutlined className="suggestion-arrow" aria-hidden="true" />
        </button>
      ))}
    </section>
  )
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
  agent: tool.agent,
  agentRunId: tool.agentRunId,
  status: tool.status,
  render_hint: tool.render_hint,
  error: tool.error,
  latencyMs: tool.latencyMs,
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

function ReplyContent({ content, tools, onChooseTravel }: {
  content: string
  tools?: ToolRun[]
  onChooseTravel?: (number: number) => void
}) {
  const results = (
    <>
      <InlineToolResults tools={tools?.filter(tool => !travelReceiptTools.has(tool.name))} />
      <TravelReceipt tools={tools?.filter(tool => travelReceiptTools.has(tool.name))} onChoose={onChooseTravel} />
    </>
  )
  const admissionsReceipt = tools?.some(tool => (
    tool.name === 'postgraduate_admissions_search' && tool.status === 'completed'
    && tool.render_hint === 'cards' && isRecord(tool.result)
    && isRecord(tool.result.data) && tool.result.data.answer === content
  ))
  if (admissionsReceipt || !content) return results
  const Message = tools?.some(tool => tool.name === mathPracticeToolName) ? MathPracticeMessage : MarkdownMessage
  return (
    <Suspense fallback={replyLoading}>
      <Message content={content} results={results} />
    </Suspense>
  )
}

interface ActiveRunConnection extends SavedAgentRun {
  conversationId: string
  lastEventId: number
}

interface ConnectionIssue {
  message: string
  canResume: boolean
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState(() => loadActiveConversationId(conversations))
  const [input, setInput] = useState('')
  const [run, dispatch] = useReducer(reduceAgentEvent, initialRunState)
  const [cancelUrl, setCancelUrl] = useState<string>()
  const [conversationsOpen, setConversationsOpen] = useState(false)
  const [directoryOpen, setDirectoryOpen] = useState(false)
  const [connectionIssue, setConnectionIssue] = useState<ConnectionIssue>()
  const [reconnecting, setReconnecting] = useState(false)
  const abortRef = useRef<AbortController | undefined>(undefined)
  const endRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<ComponentRef<typeof TextArea>>(null)
  const runRef = useRef(initialRunState)
  const activeIdRef = useRef(activeId)
  const activeConnectionRef = useRef<ActiveRunConnection | undefined>(undefined)
  const active = conversations.find((item) => item.id === activeId) ?? conversations[0]
  const latestAssistant = [...active.messages].reverse().find((message) => message.role === 'assistant')
  const awaitingFollowup = !!latestAssistant?.inputRequest && !!latestAssistant.followupKey
  const followupPanels = new Map<string, string>()
  for (const message of active.messages) {
    if (message.runId && message.followupKey) followupPanels.set(message.runId, message.id)
  }
  const awaitingPractice = !!latestAssistant?.inputRequest
    && latestAssistant.toolResults?.some(tool => tool.name === mathPracticeToolName)
  const running = run.status === 'running' || run.status === 'pending' || run.status === 'recovering'
  const currentReplyId = !running && run.runId && latestAssistant?.runId === run.runId
    ? latestAssistant.id
    : undefined
  const emptyConversation = active.messages.length === 0 && !run.assistantText && run.tools.length === 0
  const querying = run.tools.some((tool) => tool.status === 'running')

  useEffect(() => {
    saveConversations(conversations)
  }, [conversations])

  useEffect(() => {
    if (active.messages.length || run.assistantText || run.tools.length) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    } else if (endRef.current?.parentElement) {
      endRef.current.parentElement.scrollTop = 0
    }
  }, [active.messages, run.assistantText, run.tools.length])

  const setActiveConversation = (updater: (conversation: Conversation) => Conversation) => {
    setConversations((items) => items.map((item) => (item.id === activeId ? updater(item) : item)))
  }

  const applyEvent = (event: AgentEvent) => {
    runRef.current = reduceAgentEvent(runRef.current, event)
    dispatch(event)
  }

  const resetRun = (status: 'idle' | 'pending', prior?: Pick<SavedAgentRun, 'priorAgentRuns' | 'priorSkills'>) => {
    const event: AgentEvent = {
      event_id: crypto.randomUUID(),
      run_id: '',
      type: 'run.reset',
      sequence: 0,
      created_at: new Date().toISOString(),
      payload: { status, ...prior },
    }
    runRef.current = reduceAgentEvent(runRef.current, event)
    dispatch(event)
  }

  const persistRunResult = (connection: ActiveRunConnection) => {
    const state = runRef.current
    setConversations((items) => items.map((conversation) => {
      if (conversation.id !== connection.conversationId
        || conversation.activeRun?.runId !== connection.runId
        || conversation.activeRun.afterSequence !== connection.afterSequence) return conversation
      const settled = ['completed', 'cancelled', 'waiting_for_input'].includes(state.status)
      const activeRun = settled ? undefined : conversation.activeRun
      if (!state.assistantText && state.tools.length === 0 && state.agentRuns.length === 0) {
        return { ...conversation, activeRun }
      }
      const fallback = state.tools.some(tool => tool.status === 'completed')
        ? '已保留本次取得的数据，见下方。'
        : state.status === 'failed' ? '本轮执行中断。' : '本轮没有取得可展示的结果。'
      const lastUser = conversation.messages.map((message) => message.role).lastIndexOf('user')
      const existing = conversation.messages.findIndex((message, index) => index > lastUser && message.runId === connection.runId)
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: state.assistantText || fallback,
        createdAt: new Date().toISOString(),
        toolResults: persistableToolResults(state.tools),
        agentRuns: state.agentRuns,
        skills: state.skills,
        runId: connection.runId,
        inputRequest: state.humanInput,
        followupKey: state.agentRuns.some(item => item.agent === 'followup')
          ? connection.followupKey : undefined,
      }
      return {
        ...conversation,
        activeRun,
        messages: existing < 0 ? [...conversation.messages, assistantMessage]
          : conversation.messages.map((message, index) => index === existing
            ? { ...assistantMessage, id: message.id, createdAt: message.createdAt } : message),
        updatedAt: new Date().toISOString(),
      }
    }))
  }

  const consumeConnection = async (connection: ActiveRunConnection): Promise<boolean> => {
    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller
    const isCurrent = () => activeConnectionRef.current === connection && !controller.signal.aborted
    setConnectionIssue(undefined)
    try {
      const result = await streamAgentEvents(
        connection.eventsUrl,
        (event) => {
          if (isCurrent()) applyEvent(event)
        },
        {
          signal: controller.signal,
          afterEventId: connection.lastEventId,
          onEventId: (eventId) => {
            connection.lastEventId = eventId
          },
          onReconnect: () => {
            if (isCurrent()) setReconnecting(true)
          },
        },
      )
      if (!isCurrent()) return false
      if (result) connection.lastEventId = result.lastEventId
      persistRunResult(connection)
      return true
    } catch (cause) {
      if (!isCurrent() || (cause as Error).name === 'AbortError') return false
      let detail: Awaited<ReturnType<typeof getAgentRun>> | undefined
      try {
        detail = await getAgentRun(connection.runId)
      } catch {
        // The API may still be restarting. Keep the same Run for manual reconnect.
      }
      if (!isCurrent()) return false
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
        persistRunResult(connection)
      } else {
        setConnectionIssue({
          message: '连接已中断，任务可能仍在后台执行',
          canResume: status === 'RECOVERY_BLOCKED',
        })
      }
      return false
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = undefined
        setReconnecting(false)
        if (['completed', 'failed', 'cancelled'].includes(runRef.current.status)) {
          setCancelUrl(undefined)
        }
      }
    }
  }

  const attachRun = (saved: SavedAgentRun, conversationId: string) => {
    const connection = { ...saved, conversationId, lastEventId: saved.afterSequence }
    activeConnectionRef.current = connection
    setCancelUrl(saved.cancelUrl)
    return consumeConnection(connection)
  }

  const restoreActiveRun = useEffectEvent(() => {
    const saved = active.activeRun
    if (activeConnectionRef.current || !saved) return
    resetRun('pending', saved)
    void attachRun(saved, active.id)
  })

  useEffect(() => {
    saveActiveConversationId(activeId)
    const restoreTimer = window.setTimeout(restoreActiveRun, 0)
    return () => {
      window.clearTimeout(restoreTimer)
      abortRef.current?.abort()
      abortRef.current = undefined
      activeConnectionRef.current = undefined
    }
  }, [activeId])

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
      if (activeConnectionRef.current !== connection) return
      const status = String(detail?.status ?? detail?.task_status ?? '').toUpperCase()
      if (status === 'FAILED' || status === 'RECOVERY_BLOCKED') {
        await resumeAgentRun(connection.runId)
        if (activeConnectionRef.current !== connection) return
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
      if (activeConnectionRef.current !== connection) return
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
    selectConversation(conversation.id)
  }

  const send = async (action: 'answer' | 'use_available' = 'answer', followupAction?: FollowupAction) => {
    if (awaitingFollowup && !followupAction) return
    const content = followupAction ?? (action === 'use_available'
      ? '请先用现有事实生成草稿，未确认的内容保留待补充，结束本轮追问。'
      : input.trim())
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
    const pendingInput = latestAssistant?.inputRequest
    const previousRunId = latestAssistant?.runId
    const prior = pendingInput && previousRunId
      ? { priorAgentRuns: latestAssistant.agentRuns, priorSkills: latestAssistant.skills }
      : undefined
    resetRun('pending', prior)

    try {
      const followupKey = pendingInput ? latestAssistant?.followupKey : undefined
      const access: [string?] = followupKey ? [followupKey] : []
      const created = pendingInput && previousRunId
        ? await submitAgentInput(previousRunId, pendingInput.requestId, content, action, ...access)
        : await createAgentRun(history, active.id)
      const saved: SavedAgentRun = {
        ...prior,
        runId: created.run_id,
        eventsUrl: created.events_url,
        cancelUrl: created.cancel_url,
        afterSequence: created.after_sequence ?? 0,
        followupKey: followupKey ?? created.followup_key ?? undefined,
      }
      setActiveConversation((conversation) => ({ ...conversation, activeRun: saved }))
      if (activeIdRef.current === active.id) await attachRun(saved, active.id)
    } catch (cause) {
      if (activeIdRef.current !== active.id) return
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
    const connection = activeConnectionRef.current
    if (!connection || !cancelUrl) return
    try {
      await cancelAgentRun(cancelUrl)
    } catch (cause) {
      if (activeConnectionRef.current === connection) {
        setConnectionIssue({ message: (cause as Error).message, canResume: false })
      }
      return
    }
    if (activeConnectionRef.current !== connection) return
    abortRef.current?.abort()
    setCancelUrl(undefined)
    setConnectionIssue(undefined)
    applyEvent({
      event_id: crypto.randomUUID(),
      run_id: connection.runId,
      type: 'run.cancelled',
      sequence: runRef.current.lastSequence + 1,
      created_at: new Date().toISOString(),
      payload: { status: 'cancelled' },
    })
    persistRunResult(connection)
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
    setConversationsOpen(false)
    if (activeIdRef.current === id) return
    activeIdRef.current = id
    abortRef.current?.abort()
    setActiveId(id)
    setCancelUrl(undefined)
    setConnectionIssue(undefined)
    setReconnecting(false)
    activeConnectionRef.current = undefined
    resetRun('idle')
  }

  const composer = awaitingFollowup && !running && !connectionIssue && run.status !== 'failed' ? (
    <div className="chat-composer-wrap"><small>通过上方的复诊资料区继续本次整理。</small></div>
  ) : (
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
      {run.status === 'failed' && !currentReplyId ? (
        <ChatRunDetails tools={run.tools} agentRuns={run.agentRuns} />
      ) : null}
      <div className="chat-composer">
        <TextArea
          ref={composerRef}
          aria-label="给业务助理发送消息"
          value={input}
          disabled={running || awaitingFollowup}
          placeholder={awaitingPractice ? '输入选项字母、跳过或结束练习' : '输入业务问题或粘贴网址'}
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
            <Button aria-label="停止生成" danger icon={<StopOutlined />} onClick={() => void stop()} />
          </Tooltip>
        ) : (
          <Tooltip title="发送消息">
            <Button
              aria-label="发送消息"
              type="primary"
              icon={<SendOutlined />}
              disabled={!input.trim() || awaitingFollowup}
              onClick={() => void send()}
            />
          </Tooltip>
        )}
      </div>
      <small>{!running && latestAssistant?.inputRequest
        ? (awaitingPractice ? '练习进度已保存，回来可以接着答。' : '进度已保存，补充后继续本次分析。')
        : 'Enter 发送，Shift + Enter 换行'}
        {latestAssistant?.inputRequest?.allowUseAvailable && !running ? (
          <Button className="human-input-continue" type="link" size="small" onClick={() => void send('use_available')}>
            先用现有信息生成
          </Button>
        ) : null}
      </small>
    </div>
  )

  return (
    <div className={`chat-workspace${conversationsOpen ? ' conversations-open' : ''}`}>
      <button className="chat-panel-backdrop" aria-label="关闭侧边面板" onClick={() => setConversationsOpen(false)} />

      <aside className="chat-conversations" aria-label="会话列表">
        <div className="workspace-brand">
          <img className="product-logo" src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" />
          <strong>业务助理工作台</strong>
        </div>
        <div className="workspace-actions">
          <button type="button" className="new-conversation" onClick={newConversation}><PlusOutlined aria-hidden="true" />新建会话</button>
          <button type="button" onClick={() => { setConversationsOpen(false); setDirectoryOpen(true) }}><ApartmentOutlined aria-hidden="true" />专员与工具</button>
        </div>
        <div className="conversation-list product-conversations">
          <h2>最近会话</h2>
          {conversations.map((conversation) => (
            <button
              className={conversation.id === activeId ? 'conversation active' : 'conversation'}
              aria-current={conversation.id === activeId ? 'true' : undefined}
              key={conversation.id}
              onClick={() => selectConversation(conversation.id)}
            >
              <span>{conversation.title}</span>
            </button>
          ))}
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
            <strong>{active.title}</strong>
          </div>
          <div className="chat-title-actions">
            {run.status !== 'idle' && <Tag color={statusColor}>{statusLabels[run.status] ?? run.status}</Tag>}
          </div>
        </header>

        <div className="chat-messages">
          {emptyConversation ? (
            <div className="chat-welcome">
              <div className="welcome-hero">
                <div className="welcome-intro">
                  <h1>今天想查什么？</h1>
                  <p>检查网页、查公开资料、练数学、整理复诊记录或安排出差。</p>
                </div>
                <img
                  className="welcome-illustration"
                  src={`${import.meta.env.BASE_URL}specialists-anime.webp`}
                  width={850}
                  height={567}
                  alt=""
                  decoding="async"
                />
              </div>
              {composer}
              <div className="suggestions" role="group" aria-label="业务示例">
                {starterGroups.map(group => (
                  <ExampleGroup key={group.title} group={group} onChoose={prompt => {
                    setInput(prompt)
                    composerRef.current?.focus()
                  }} />
                ))}
              </div>
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
                    <AssistantRunStatus
                      agentRuns={item.agentRuns}
                      skills={item.skills}
                      tools={item.id === currentReplyId ? run.tools : item.toolResults}
                    />
                    <ChatRunDetails
                      tools={item.id === currentReplyId ? run.tools : item.toolResults}
                      agentRuns={item.agentRuns}
                    />
                    <ReplyContent
                      content={item.content}
                      tools={item.toolResults}
                      onChooseTravel={item.id === latestAssistant?.id && item.inputRequest && !running
                        ? number => {
                          setInput(`保存方案 ${number}`)
                          composerRef.current?.focus({ cursor: 'end' })
                        }
                        : undefined}
                    />
                    {item.runId && item.followupKey && followupPanels.get(item.runId) === item.id && (
                      <Suspense fallback={replyLoading}>
                        <FollowupPanel
                          runId={item.runId} access={item.followupKey}
                          canContinue={item.id === latestAssistant?.id && !!item.inputRequest && !running}
                          onContinue={choice => void send('answer', choice)}
                        />
                      </Suspense>
                    )}
                  </>
                ) : item.content}
              </div>
            </article>
          ))}

          {running ? (
            <article className="chat-message assistant streaming">
              <div className="avatar agent-avatar" aria-hidden="true"><BulbOutlined /></div>
              <div className="chat-message-content">
                <AssistantRunStatus agentRuns={run.agentRuns} skills={run.skills} tools={run.tools} />
                {!run.assistantText || querying ? (
                  <div className="agent-activity" role="status" aria-label="分析进度" aria-live="polite">
                    <div className="agent-activity-title">
                      <strong>{run.stage}</strong>
                      <span className="activity-dots" aria-hidden="true"><i /><i /><i /></span>
                    </div>
                  </div>
                ) : null}
                <ChatRunDetails tools={run.tools} agentRuns={run.agentRuns} />
                <ReplyContent content={run.assistantText} tools={run.tools} />
                {run.assistantText ? <span className="cursor" /> : null}
              </div>
            </article>
          ) : null}
          <div ref={endRef} />
        </div>

        {!emptyConversation && composer}
      </section>
      {directoryOpen && <Suspense fallback={null}><SpecialistDirectory conversationId={active.id} onClose={() => setDirectoryOpen(false)} /></Suspense>}
    </div>
  )
}
