import {
  ApartmentOutlined,
  ArrowDownOutlined,
  BookOutlined,
  CloseOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  MenuOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  MonitorOutlined,
  ProfileOutlined,
  QuestionCircleFilled,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Grid, Input, Tooltip } from 'antd'
import { type ComponentRef, lazy, Suspense, useEffect, useEffectEvent, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react'

import { AssistantRunStatus } from '../components/AssistantRunStatus'
import { ChatAttachmentControls, DocumentChips } from '../components/ChatAttachments'
import { ExecutionTimeline } from '../components/ChatRunDetails'
import { InlineToolResults } from '../components/InlineToolResults'
import { TravelReceipt } from '../components/TravelReceipt'
import { ConversationHistory, WorkspaceActivity } from '../components/WorkspaceActivity'
import { WorkspaceHome } from '../components/WorkspaceHome'
import {
  cancelAgentRun,
  createAgentRun,
  getAgentRun,
  getWorkspaceRunStats,
  resumeAgentRun,
  submitAgentInput,
  streamAgentEvents,
  type AgentRunDetail,
  type WorkspaceRunStats,
} from '../services/agentApi'
import type { FollowupAction } from '../services/followupApi'
import { downloadDocument, MAX_DOCUMENTS_PER_TURN, uploadDocument } from '../services/workspaceApi'
import { initialRunState, readRunSnapshot, reduceAgentEvent } from '../state/agentState'
import { replyStatus, taskStatus } from '../state/taskStatus'
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
  HumanInputRequest,
  SavedAgentRun,
  RunState,
  ToolRun,
} from '../types/agent'
import type { DocumentSummary } from '../types/workspace'

const { TextArea } = Input
const SpecialistDirectory = lazy(() => import('../components/SpecialistDirectory'))
const FollowupPanel = lazy(() => import('../components/FollowupPanel'))
const MathPracticeMessage = lazy(() => import('../components/MathPracticeMessage'))
const KnowledgePage = lazy(() => import('./KnowledgePage'))
const DataSourcesPage = lazy(() => import('./DataSourcesPage'))
const ObservabilityPage = lazy(async () => {
  const module = await import('./ObservabilityPage')
  return { default: module.ObservabilityPage }
})
const MarkdownMessage = lazy(async () => {
  const module = await import('../components/MarkdownMessage')
  return { default: module.MarkdownMessage }
})
const replyLoading = <div className="agent-activity" role="status">正在加载回复…</div>
const mathPracticeToolName = 'tal_math_practice'
const travelReceiptTools = new Set(['travel_weather', 'travel_compare_plans', 'travel_save_plan'])

const views = {
  '#/tasks': 'tasks',
  '#/history': 'history',
  '#/capabilities': 'capabilities',
  '#/observability': 'observability',
  '#/knowledge': 'knowledge',
  '#/sources': 'sources',
} as const

type WorkspaceView = 'chat' | (typeof views)[keyof typeof views]

function workspaceView(): WorkspaceView {
  return views[window.location.hash as keyof typeof views] ?? 'chat'
}

const timeLabel = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })

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

function withoutRepeatedQuestions(content: string, request?: HumanInputRequest) {
  const questions = request?.questions
  if (!questions?.length) return content
  const lines = content.trimEnd().split('\n')
  const tail = lines.slice(-questions.length)
  const duplicates = tail.length === questions.length && tail.every((line, index) =>
    line.trim().replace(/^(?:\d+[.)、]|[-*+])\s+/, '') === questions[index].trim(),
  )
  return duplicates ? lines.slice(0, -questions.length).join('\n').trimEnd()
    .replace(/(?:^|\n)还需要你补充以下信息[：:]$/, '').trimEnd() : content
}

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
  const hasExamNotice = tools?.some(tool => tool.name === 'ntce_written_exam_lookup' && tool.status === 'completed'
    && isRecord(tool.result) && isRecord(tool.result.data) && tool.result.data.matched === true)
  const reply = <Message content={content} results={results} />
  return (
    <Suspense fallback={replyLoading}>
      {hasExamNotice ? <div className="workspace-exam-receipt">
        <img src={`${import.meta.env.BASE_URL}workbench-exam.webp`} alt="" width={128} height={84} />
        {reply}
      </div> : reply}
    </Suspense>
  )
}

interface ActiveRunConnection extends SavedAgentRun {
  conversationId: string
  lastEventId: number
}

interface ConnectionIssue {
  message: string
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState(() => loadActiveConversationId(conversations))
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [view, setView] = useState(workspaceView)
  const [run, dispatch] = useReducer((state: RunState, update: AgentEvent | RunState) =>
    'stage' in update ? update : reduceAgentEvent(state, update), initialRunState)
  const [cancelUrl, setCancelUrl] = useState<string>()
  const [conversationsOpen, setConversationsOpen] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState(() => localStorage.getItem('workspace-nav-collapsed') === 'true')
  const dialogRef = useRef<HTMLDivElement>(null)
  const [dialogWidth, setDialogWidth] = useState(0)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [uploading, setUploading] = useState<string[]>([])
  const [documentError, setDocumentError] = useState<{ conversationId: string, message: string }>()
  const [detailsOpen, setDetailsOpen] = useState<boolean>()
  const [historyOpen, setHistoryOpen] = useState<boolean>()
  const [awayFromLatest, setAwayFromLatest] = useState(false)
  const [detailMessageId, setDetailMessageId] = useState<string>()
  const [taskTitle, setTaskTitle] = useState<string>()
  const [verified, setVerified] = useState<AgentRunDetail>()
  const [historicDetail, setHistoricDetail] = useState<AgentRunDetail>()
  const [inspectedRunId, setInspectedRunId] = useState<string | undefined>(() =>
    sessionStorage.getItem(`workspace-task:${activeId}`) ?? undefined)
  const [connectionIssue, setConnectionIssue] = useState<ConnectionIssue>()
  const [reconnecting, setReconnecting] = useState(false)
  const [runStats, setRunStats] = useState<{ runId: string, value: WorkspaceRunStats }>()
  const abortRef = useRef<AbortController | undefined>(undefined)
  const messagesRef = useRef<HTMLDivElement>(null)
  const messageListRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const readingResult = useRef(false)
  const inspectedReplyRef = useRef<HTMLElement>(null)
  const composerRef = useRef<ComponentRef<typeof TextArea>>(null)
  const mobileMenuRef = useRef<ComponentRef<typeof Button>>(null)
  const newConversationRef = useRef<HTMLButtonElement>(null)
  const runRef = useRef(initialRunState)
  const activeIdRef = useRef(activeId)
  const activeConnectionRef = useRef<ActiveRunConnection | undefined>(undefined)
  const active = conversations.find((item) => item.id === activeId) ?? conversations[0]
  const draftDocuments = active.draftDocuments ?? []
  const input = drafts[active.id] ?? ''
  const setInput = (value: string) => setDrafts(items => ({ ...items, [active.id]: value }))
  const replies = active.messages.filter(message => message.role === 'assistant')
  const latestAssistant = replies.at(-1)
  const inspectedReply = inspectedRunId ? replies.filter(message => message.runId === inspectedRunId).at(-1) : undefined
  const taskReply = inspectedRunId ? inspectedReply : latestAssistant
  const awaitingFollowup = !!taskReply?.inputRequest && !!taskReply.followupKey
  const followupPanels = new Map<string, string>()
  for (const message of active.messages) {
    if (message.runId && message.followupKey) followupPanels.set(message.runId, message.id)
  }
  const awaitingPractice = !!taskReply?.inputRequest
    && taskReply.toolResults?.some(tool => tool.name === mathPracticeToolName)
  const running = run.status === 'running' || run.status === 'pending' || run.status === 'recovering'
  const lastMessage = active.messages.at(-1)
  const lastUser = active.messages.filter(message => message.role === 'user').at(-1)
  const currentReplyId = !running && run.runId && lastMessage?.role === 'assistant' && lastMessage.runId === run.runId
    ? lastMessage.id
    : undefined
  const showLiveReply = running || Boolean(!currentReplyId && run.runId
    && (run.assistantText || run.tools.length || run.agentRuns.length))
  const liveId = active.activeRun?.replyId ?? `${lastUser?.id}-reply`
  const liveMessage: ChatMessage = { id: liveId, role: 'assistant', createdAt: '', content: run.assistantText,
    agentRuns: run.agentRuns, toolResults: run.tools, runId: run.runId }
  const messages = !showLiveReply ? active.messages : active.messages.some(item => item.id === liveId)
    ? active.messages.map(item => item.id === liveId ? liveMessage : item) : [...active.messages, liveMessage]
  const emptyConversation = active.messages.length === 0 && !run.assistantText && run.tools.length === 0
  const uploadingHere = uploading.includes(active.id)
  const canAttach = !running && !taskReply?.inputRequest && !active.activeRun
  const isConversation = view === 'chat' && !emptyConversation
  const { lg, xxl } = Grid.useBreakpoint()
  const showHistory = historyOpen ?? Boolean(lg)
  const dockHistory = lg && showHistory
  const dockDetails = dialogWidth >= (dockHistory ? 1310 : 1080)
  const showDetails = detailsOpen ?? Boolean(xxl && dockDetails)
  const currentStatus = run.status === 'idle' || (inspectedRunId && inspectedRunId !== run.runId && !running)
    ? replyStatus(taskReply) ?? 'idle'
    : run.status
  const partial = currentStatus === 'completed' && (run.runId === taskReply?.runId && run.status !== 'idle' ? run.partial : taskReply?.partial)
  const [statusLabel, statusTone] = taskStatus(currentStatus, partial)
  const pendingInput = running || awaitingFollowup || currentStatus !== 'waiting_for_input' ? undefined : taskReply?.inputRequest
  const failed = currentStatus === 'failed' || currentStatus === 'recovery_blocked'
  const actionRunId = inspectedRunId ?? run.runId ?? taskReply?.runId
  const firstReplyIndex = actionRunId ? active.messages.findIndex(message => message.runId === actionRunId) : -1
  const taskQuestion = firstReplyIndex >= 0
    ? active.messages.slice(0, firstReplyIndex).reverse().find(message => message.role === 'user') : lastUser
  const heading = taskTitle ?? (taskQuestion?.documents?.length ? '资料问答' : taskQuestion?.content.slice(0, 120)) ?? active.title
  const canResume = failed && verified?.id === actionRunId && !!verified?.checkpoint
    && ['FAILED', 'RECOVERY_BLOCKED'].includes(verified.status.toUpperCase())
  const conversationsByRun = useMemo(() => {
    const byRun = new Map<string, Conversation>()
    if (view === 'observability') {
      for (const conversation of conversations) {
        for (const message of conversation.messages) {
          if (message.runId) byRun.set(message.runId, conversation)
        }
        if (conversation.activeRun) byRun.set(conversation.activeRun.runId, conversation)
      }
    }
    return byRun
  }, [conversations, view])

  useEffect(() => {
    const followRoute = () => {
      setView(workspaceView())
      setConversationsOpen(false)
    }
    window.addEventListener('hashchange', followRoute)
    return () => window.removeEventListener('hashchange', followRoute)
  }, [])

  useEffect(() => {
    if (!conversationsOpen) return
    newConversationRef.current?.focus()
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setConversationsOpen(false)
      mobileMenuRef.current?.focus()
    }
    window.addEventListener('keydown', onEscape)
    return () => window.removeEventListener('keydown', onEscape)
  }, [conversationsOpen])

  useEffect(() => {
    saveConversations(conversations)
  }, [conversations])

  useEffect(() => {
    const key = `workspace-task:${activeId}`
    if (inspectedRunId) sessionStorage.setItem(key, inspectedRunId)
    else sessionStorage.removeItem(key)
  }, [activeId, inspectedRunId])

  useLayoutEffect(() => {
    const pane = messagesRef.current
    if (!isConversation || !pane) return
    const inspected = inspectedReplyRef.current
    messageListRef.current?.style.removeProperty('min-height')
    readingResult.current = Boolean(inspected)
    followLatest.current = !inspected
    pane.scrollTop = inspected
      ? pane.scrollTop + inspected.getBoundingClientRect().bottom - pane.getBoundingClientRect().bottom
      : pane.scrollHeight
  }, [isConversation, active.id, inspectedReply?.id])

  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!isConversation || !dialog) return
    const measure = () => setDialogWidth(dialog.clientWidth)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(dialog)
    return () => observer.disconnect()
  }, [isConversation])

  useLayoutEffect(() => {
    const pane = messagesRef.current
    const list = messageListRef.current
    if (!isConversation || !pane || !list) return
    const observer = new ResizeObserver(() => {
      if (followLatest.current && !readingResult.current) pane.scrollTop = pane.scrollHeight
    })
    observer.observe(pane)
    observer.observe(list)
    return () => observer.disconnect()
  }, [isConversation])

  const setActiveConversation = (updater: (conversation: Conversation) => Conversation) => {
    setConversations((items) => items.map((item) => (item.id === activeId ? updater(item) : item)))
  }

  const preserveReadingPosition = (pane: HTMLElement) => {
    readingResult.current = true
    const list = messageListRef.current
    if (!list || pane.scrollTop <= 0) return
    // Reserve scroll range at the end of the transcript, outside the active result card.
    const style = getComputedStyle(pane)
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)
    list.style.minHeight = `${Math.max(0, pane.scrollTop + pane.clientHeight - padding)}px`
  }

  const showDocumentError = (message: string) => setDocumentError({ conversationId: active.id, message })
  const attachDocument = (document: DocumentSummary) => {
    if (draftDocuments.some(item => item.id === document.id)) return
    if (draftDocuments.length >= MAX_DOCUMENTS_PER_TURN) {
      showDocumentError('每条消息最多携带 4 份资料。')
      return
    }
    setActiveConversation(conversation => ({
      ...conversation, draftDocuments: [...(conversation.draftDocuments ?? []), document],
    }))
    setDocumentError(undefined)
  }

  const uploadFiles = async (files: File[]) => {
    if (!canAttach || uploadingHere) return
    const room = MAX_DOCUMENTS_PER_TURN - draftDocuments.length
    if (files.length > room) {
      showDocumentError('每条消息最多携带 4 份资料。')
      return
    }
    setDocumentError(undefined)
    setUploading(ids => [...ids, active.id])
    const failed: string[] = []
    for (const file of files) {
      try {
        const uploaded = await uploadDocument(file, active.id)
        setActiveConversation(conversation => {
          const current = conversation.draftDocuments ?? []
          return current.some(item => item.id === uploaded.id)
            ? conversation : { ...conversation, draftDocuments: [...current, uploaded] }
        })
      } catch (cause) {
        failed.push(`${file.name} · ${(cause as Error).message}`)
      }
    }
    setUploading(ids => ids.filter(id => id !== active.id))
    if (failed.length) showDocumentError(failed.join(' '))
  }

  const download = (document: DocumentSummary) =>
    void downloadDocument(document).catch((cause: Error) => showDocumentError(cause.message))

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
      const settled = ['completed', 'cancelled', 'waiting_for_input', 'failed', 'recovery_blocked'].includes(state.status)
      const activeRun = settled ? undefined : conversation.activeRun
      const fallback = state.tools.some(tool => tool.status === 'completed')
        ? '已保留本次取得的数据，见下方。'
        : state.status === 'cancelled' ? '本轮已停止。'
          : state.status === 'failed' || state.status === 'recovery_blocked' ? '' : '本轮没有取得可展示的结果。'
      const lastUser = conversation.messages.map((message) => message.role).lastIndexOf('user')
      const existing = conversation.messages.findIndex((message, index) => message.id === connection.replyId
        || index > lastUser && message.runId === connection.runId)
      const assistantMessage: ChatMessage = {
        id: connection.replyId ?? `${conversation.messages[lastUser]?.id}-reply`,
        role: 'assistant',
        content: state.assistantText || fallback,
        createdAt: new Date().toISOString(),
        toolResults: persistableToolResults(state.tools),
        agentRuns: state.agentRuns,
        skills: state.skills,
        runId: connection.runId,
        runStatus: state.status,
        afterSequence: connection.afterSequence,
        throughSequence: state.lastSequence,
        error: state.error,
        partial: state.partial,
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
      const hasReply = detail?.events?.some(item => ['message.completed', 'run.waiting_for_input']
        .includes(String(item.event_type ?? item.type)))
      if (detail && (['FAILED', 'RECOVERY_BLOCKED', 'CANCELLED'].includes(status)
        || hasReply && ['COMPLETED', 'WAITING_FOR_INPUT'].includes(status))) {
        setVerified(detail)
        runRef.current = readRunSnapshot(detail, connection.afterSequence, runRef.current)
        dispatch(runRef.current)
        persistRunResult(connection)
      } else {
        setConnectionIssue({
          message: '连接已中断，任务可能仍在后台执行',
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

  const reconnectActiveRun = async (retry = false) => {
    if (reconnecting || (retry && (!canResume || !actionRunId))) return
    let connection = activeConnectionRef.current
    if (retry && connection?.runId !== actionRunId && actionRunId) {
      const url = `/api/v1/chat/runs/${encodeURIComponent(actionRunId)}`
      connection = { runId: actionRunId, conversationId: active.id, eventsUrl: `${url}/events`,
        cancelUrl: `${url}/cancel`, afterSequence: 0, lastEventId: 0, followupKey: taskReply?.followupKey }
      activeConnectionRef.current = connection
    }
    if (!connection) return
    setReconnecting(true)
    setConnectionIssue(undefined)
    try {
      if (retry) {
        const state = readRunSnapshot(verified!, connection.afterSequence)
        await resumeAgentRun(connection.runId)
        if (activeConnectionRef.current !== connection) return
        connection = { ...connection, replyId: taskReply?.id, lastEventId: Math.max(state.lastSequence, connection.lastEventId) }
        activeConnectionRef.current = connection
        const saved = { ...connection }
        setActiveConversation(conversation => ({ ...conversation, activeRun: saved }))
        runRef.current = { ...(state.tools.length || runRef.current.runId !== connection.runId ? state : runRef.current),
          status: 'recovering', stage: '正在恢复任务', error: undefined }
        dispatch(runRef.current)
        setCancelUrl(connection.cancelUrl)
        setVerified(undefined)
      }
      await consumeConnection(connection)
    } catch (cause) {
      if (activeConnectionRef.current !== connection) return
      if (retry) {
        const detail = await getAgentRun(connection.runId).catch(() => undefined)
        if (activeConnectionRef.current !== connection) return
        if (detail?.status.toUpperCase() === 'RECOVERY_BLOCKED') {
          setVerified(detail)
          runRef.current = readRunSnapshot(detail)
          dispatch(runRef.current)
          setActiveConversation(conversation => ({ ...conversation, messages: conversation.messages.map(item =>
            item.id === taskReply?.id ? { ...item, runStatus: 'recovery_blocked', error: detail.error_message ?? undefined } : item) }))
          setReconnecting(false)
          return
        }
      }
      setConnectionIssue({
        message: (cause as Error).message || '重新连接失败',
      })
      setReconnecting(false)
    }
  }

  const newConversation = () => {
    if (emptyConversation && !input.trim() && !active.activeRun && !draftDocuments.length && !uploadingHere) {
      selectConversation(active.id)
      return
    }
    const conversation = createConversation()
    setConversations((items) => [conversation, ...items])
    selectConversation(conversation.id)
  }

  const startWithDocument = (document: DocumentSummary) => {
    const conversation = { ...createConversation(), draftDocuments: [document] }
    setConversations(items => [conversation, ...items])
    selectConversation(conversation.id)
  }

  const send = async (action: 'answer' | 'use_available' = 'answer', followupAction?: FollowupAction) => {
    if (awaitingFollowup && !followupAction) return
    const documents = canAttach ? draftDocuments : []
    const content = followupAction ?? (action === 'use_available'
      ? '请先用现有事实生成草稿，未确认的内容保留待补充，结束本轮追问。'
      : input.trim() || (documents.length ? '请概括所选资料，并给出关键事实及出处。' : ''))
    if (!content || running || uploadingHere || (inspectedRunId && !taskReply)) return

    followLatest.current = true
    readingResult.current = false
    messageListRef.current?.style.removeProperty('min-height')
    setAwayFromLatest(false)
    setDetailMessageId(undefined)
    setTaskTitle(undefined)
    setInput('')
    setInspectedRunId(undefined)
    setConnectionIssue(undefined)
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
      ...(documents.length ? { documents } : {}),
    }
    const history = [...active.messages, userMessage]
    setActiveConversation((conversation) => ({
      ...conversation,
      title: conversation.messages.length === 0 ? content.slice(0, 28) : conversation.title,
      messages: history,
      updatedAt: new Date().toISOString(),
    }))
    const request = currentStatus === 'waiting_for_input' ? taskReply?.inputRequest : undefined
    const previousRunId = taskReply?.runId
    const prior = request && previousRunId
      ? { priorAgentRuns: taskReply.agentRuns, priorSkills: taskReply.skills }
      : undefined
    resetRun('pending', prior)

    try {
      const followupKey = request ? taskReply?.followupKey : undefined
      const access: [string?] = followupKey ? [followupKey] : []
      const created = request && previousRunId
        ? await submitAgentInput(previousRunId, request.requestId, content, action, ...access)
        : await createAgentRun(history, active.id)
      const saved: SavedAgentRun = {
        replyId: `${userMessage.id}-reply`,
        ...prior,
        runId: created.run_id,
        eventsUrl: created.events_url,
        cancelUrl: created.cancel_url,
        afterSequence: created.after_sequence ?? 0,
        followupKey: followupKey ?? created.followup_key ?? undefined,
      }
      setActiveConversation((conversation) => ({
        ...conversation, activeRun: saved,
        ...(documents.length ? { draftDocuments: [] } : {}),
      }))
      if (activeIdRef.current === active.id) await attachRun(saved, active.id)
    } catch (cause) {
      if (documents.length || request) {
        setActiveConversation(conversation => ({
          ...conversation, title: active.title, updatedAt: active.updatedAt,
          messages: conversation.messages.filter(message => message.id !== userMessage.id),
        }))
        setInput(input)
        if (request && activeIdRef.current === active.id) {
          setInspectedRunId(previousRunId)
          resetRun('idle')
          showDocumentError((cause as Error).message)
          return
        }
      }
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
      const receipt = await cancelAgentRun(cancelUrl)
      // A completion can win the race with a stop. Keep its actual terminal event.
      if (receipt.status !== 'cancelled') return
    } catch (cause) {
      if (activeConnectionRef.current === connection) {
        setConnectionIssue({ message: (cause as Error).message })
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

  const selectConversation = (id: string, runId?: string, title?: string) => {
    setConversationsOpen(false)
    if (!lg) setHistoryOpen(false)
    setTaskTitle(title)
    setDetailMessageId(undefined)
    setAwayFromLatest(false)
    setLibraryOpen(false)
    setInspectedRunId(runId)
    if (view !== 'chat') {
      setView('chat')
      window.location.hash = '#/chat'
    }
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
          description={failed ? undefined : '重新连接可以查看这次任务的最新进度。'}
          action={(
            <Button size="small" loading={reconnecting} onClick={() => void reconnectActiveRun(canResume)}>
              {canResume ? '恢复任务' : '重新连接'}
            </Button>
          )}
        />
      ) : null}
      {failed && !connectionIssue ? (
        <Alert
          type="error"
          showIcon
          title={(run.runId === actionRunId ? run.error : undefined) ?? (verified?.id === actionRunId ? verified?.error_message : undefined) ?? taskReply?.error ?? statusLabel}
          description={canResume ? '可从本次任务的检查点继续。' : undefined}
          action={canResume ? (
            <Button size="small" loading={reconnecting} onClick={() => void reconnectActiveRun(true)}>
              恢复任务
            </Button>
          ) : undefined}
        />
      ) : null}
      {documentError?.conversationId === active.id && (
        <Alert type="error" showIcon title={documentError.message} closable onClose={() => setDocumentError(undefined)} />
      )}
      <div className={`workspace-input-surface${pendingInput ? ' awaiting-input' : ''}`}>
        {pendingInput && (
          <div className="workspace-input-request">
            <h2><QuestionCircleFilled aria-hidden="true" />{awaitingPractice ? '等待作答' : '等待补充'}</h2>
            {pendingInput.questions.length > 0 && (
              <ul>{pendingInput.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
            )}
          </div>
        )}
        {draftDocuments.length > 0 && canAttach && (
          <div className="workspace-attachment-draft">
            <DocumentChips documents={draftDocuments} onDownload={download} onRemove={id => setActiveConversation(conversation => ({
              ...conversation, draftDocuments: conversation.draftDocuments?.filter(document => document.id !== id),
            }))} />
            <small>选中的资料会随本条问题发送给对话模型。</small>
          </div>
        )}
        <div className={`chat-composer${canAttach ? ' with-attachments' : ''}`}>
          {emptyConversation && <SearchOutlined className="workspace-composer-icon" aria-hidden="true" />}
          <TextArea
            ref={composerRef}
            name="message"
            autoComplete="off"
            aria-label="给业务助理发送消息"
            value={input}
            disabled={running || awaitingFollowup}
            placeholder={awaitingPractice ? '输入选项字母、跳过或结束练习'
              : pendingInput ? '输入补充信息…' : emptyConversation ? '请输入需求、粘贴网址或上传资料…' : '继续与主 Agent 对话…'}
            autoSize={{ minRows: 1, maxRows: 5 }}
            onChange={(event) => setInput(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                event.preventDefault()
                void send()
              }
            }}
          />
          {canAttach && <ChatAttachmentControls busy={uploadingHere} onUpload={files => void uploadFiles(files)} onLibrary={() => setLibraryOpen(true)} />}
          {running ? (
            <Tooltip title="停止生成">
              <Button aria-label="停止生成" danger icon={<StopOutlined />} disabled={!cancelUrl} onClick={() => void stop()} />
            </Tooltip>
          ) : (
            <Tooltip title={pendingInput ? '提交补充并继续' : '发送消息'}>
              <Button
                aria-label="发送消息"
                type="primary"
                icon={<SendOutlined />}
                disabled={(!input.trim() && (!canAttach || !draftDocuments.length)) || awaitingFollowup || uploadingHere}
                onClick={() => void send()}
              />
            </Tooltip>
          )}
        </div>
        {!emptyConversation && (
          <small className="workspace-composer-note">
            {pendingInput ? (awaitingPractice ? '练习进度已保存，回来可以接着答。' : '进度已保存，补充后继续本次分析。') : 'Enter 发送 · Shift + Enter 换行'}
            {pendingInput?.allowUseAvailable && (
              <Button className="human-input-continue" type="link" size="small" onClick={() => void send('use_available')}>
                先用现有信息生成
              </Button>
            )}
          </small>
        )}
      </div>
    </div>
  )

  const detailReply = replies.find(message => message.id === detailMessageId) ?? taskReply
  const liveDetails = !detailMessageId && (!inspectedReply || inspectedRunId === run.runId) && showLiveReply
  const recordedTools = liveDetails || (currentReplyId && detailReply?.id === currentReplyId) ? run.tools : detailReply?.toolResults
  const detailStatus = replyStatus(detailReply)
  const statsRunId = view === 'chat' && showDetails && !liveDetails ? detailReply?.runId : undefined
  const lookupId = isConversation && !running ? (inspectedRunId ?? (failed || run.status === 'idle' ? actionRunId : undefined) ?? statsRunId) : undefined
  const syncStoredReply = useEffectEvent((detail: AgentRunDetail) => {
    if (!Array.isArray(detail.events) || activeConnectionRef.current && abortRef.current) return
    const snapshot = readRunSnapshot(detail)
    const previous = replies.filter(message => message.runId === detail.id).at(-1)
    if (['running', 'pending', 'recovering'].includes(snapshot.status)) {
      if (active.activeRun || !inspectedRunId && previous !== latestAssistant) return
      const url = `/api/v1/chat/runs/${encodeURIComponent(detail.id)}`
      const saved = { runId: detail.id, eventsUrl: `${url}/events`, cancelUrl: `${url}/cancel`, afterSequence: 0,
        followupKey: previous?.followupKey }
      setActiveConversation(conversation => ({ ...conversation, activeRun: saved }))
      resetRun('pending')
      void attachRun(saved, active.id)
      return
    }
    if (!detail.events.length) return
    if (previous && previous.runStatus === snapshot.status && previous.error === snapshot.error
      && previous.inputRequest?.requestId === snapshot.humanInput?.requestId && !!previous.partial === !!snapshot.partial) return
    const turnStart = [...detail.events].reverse().find(item => (item.event_type ?? item.type) === 'input.submitted')?.sequence ?? 0
    const turn = readRunSnapshot(detail, turnStart)
    const message: ChatMessage = { ...previous, id: previous?.id ?? crypto.randomUUID(), role: 'assistant', runId: detail.id,
      createdAt: previous?.createdAt ?? detail.events.at(-1)!.created_at,
      content: turn.assistantText || previous?.content || '', runStatus: snapshot.status, error: snapshot.error,
      partial: snapshot.partial, inputRequest: snapshot.status === 'waiting_for_input' ? snapshot.humanInput : undefined,
      afterSequence: turnStart, throughSequence: snapshot.lastSequence,
      toolResults: persistableToolResults(turn.tools), agentRuns: snapshot.agentRuns, skills: snapshot.skills }
    setActiveConversation(conversation => ({ ...conversation,
      messages: previous ? conversation.messages.map(item => item.id === previous.id ? message : item)
        : [...conversation.messages, message].sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
      activeRun: conversation.activeRun?.runId === detail.id ? undefined : conversation.activeRun,
    }))
    if (runRef.current.runId === detail.id && runRef.current.status !== snapshot.status) {
      runRef.current = turn
      dispatch(turn)
    }
  })
  useEffect(() => {
    if (!lookupId) return
    const request = new AbortController()
    void getAgentRun(lookupId, request.signal).then(detail => {
      if (request.signal.aborted || detail?.id !== lookupId) return
      setVerified(detail)
      syncStoredReply(detail)
    }).catch(() => undefined)
    return () => request.abort()
  }, [lookupId, currentStatus, active.id, view])
  useEffect(() => {
    if (!statsRunId || statsRunId === lookupId) return
    const request = new AbortController()
    void getAgentRun(statsRunId, request.signal).then(detail => {
      if (!request.signal.aborted && detail?.id === statsRunId) setHistoricDetail(detail)
    }).catch(() => undefined)
    return () => request.abort()
  }, [statsRunId, lookupId])
  const detailTools = useMemo(() => {
    const detail = statsRunId === lookupId ? verified : historicDetail
    if (!recordedTools || !detail || detail.id !== statsRunId) return recordedTools
    const recorded = readRunSnapshot(detail, detailReply?.afterSequence, undefined, detailReply?.throughSequence)
    const ledger = new Map(recorded.tools.map(tool => [tool.id, tool]))
    return recordedTools.map(tool => ({ ...tool, arguments: tool.arguments ?? ledger.get(tool.id)?.arguments,
      result: ledger.get(tool.id)?.result ?? tool.result }))
  }, [recordedTools, verified, historicDetail, statsRunId, lookupId, detailReply?.afterSequence, detailReply?.throughSequence])
  useEffect(() => {
    if (inspectedRunId && pendingInput) composerRef.current?.focus({ preventScroll: true })
  }, [inspectedRunId, pendingInput])
  useEffect(() => {
    if (!statsRunId) return
    const request = new AbortController()
    void getWorkspaceRunStats(statsRunId, request.signal).then(value => {
      if (!request.signal.aborted) setRunStats({ runId: statsRunId, value })
    }).catch(() => undefined)
    return () => request.abort()
  }, [statsRunId, detailStatus])
  const detailStats = runStats?.runId === statsRunId ? runStats?.value : undefined
  const replyIndex = detailReply ? active.messages.indexOf(detailReply) : -1
  const relatedTurn = active.messages.slice(0, !liveDetails && replyIndex >= 0 ? replyIndex : undefined)
    .reverse().find(message => message.role === 'user')
  const timeline = (
    <ExecutionTimeline
      stage={liveDetails ? run.stage : detailStatus && taskStatus(detailStatus, detailReply?.partial)[0]}
      started={Boolean(liveDetails || detailReply?.runId)}
      agentRuns={liveDetails ? run.agentRuns : detailReply?.agentRuns}
      skills={liveDetails ? run.skills : detailReply?.skills}
      tools={detailTools}
      running={liveDetails && running}
      status={liveDetails ? run.status : detailStatus}
      partial={!liveDetails && detailReply?.partial}
      elapsedMs={detailStats?.elapsed_ms}
      totalTokens={detailStats?.total_tokens}
      documents={relatedTurn?.documents}
      onDownload={download}
    />
  )

  return (
    <div className={`chat-workspace${navCollapsed ? ' workspace-nav-compact' : ''}${isConversation ? ' workspace-in-conversation' : ''}${view === 'observability' ? ' workspace-observing' : ''}${conversationsOpen ? ' conversations-open' : ''}`}>
      <a className="workspace-skip" href="#workspace-main" onClick={event => {
        event.preventDefault()
        document.getElementById('workspace-main')?.focus()
      }}>跳到工作区</a>
      <button type="button" className="chat-panel-backdrop" aria-label="关闭导航" tabIndex={-1} onClick={() => {
        setConversationsOpen(false)
        mobileMenuRef.current?.focus()
      }} />
      <aside className="chat-conversations" aria-label="工作台侧栏">
        <div className="workspace-brand">
          <img src={`${import.meta.env.BASE_URL}workbench-mark.webp`} width={36} height={36} alt="" />
          <strong>AI Agent 工作台</strong>
        </div>
        <nav id="workspace-global-nav" className="workspace-actions" aria-label="工作台导航">
          <button ref={newConversationRef} type="button" className="new-conversation" aria-label="新建对话" title="新建对话" onClick={newConversation}>
            <MessageOutlined aria-hidden="true" /><span>新建对话</span>
          </button>
          <a href="#/tasks" aria-label="任务工作台" title="任务工作台" aria-current={view === 'tasks' ? 'page' : undefined}>
            <ProfileOutlined aria-hidden="true" /><span>任务工作台</span>
          </a>
          <a href="#/history" aria-label="历史会话" title="历史会话" aria-current={view === 'history' ? 'page' : undefined}>
            <HistoryOutlined aria-hidden="true" /><span>历史会话</span>
          </a>
          <a href="#/capabilities" aria-label="Agent 能力" title="Agent 能力" aria-current={view === 'capabilities' ? 'page' : undefined}
            onClick={() => setConversationsOpen(false)}>
            <ApartmentOutlined aria-hidden="true" /><span>Agent 能力</span>
          </a>
          <a href="#/observability" aria-label="运行中心" title="运行中心" aria-current={view === 'observability' ? 'page' : undefined}>
            <MonitorOutlined aria-hidden="true" /><span>运行中心</span>
          </a>
          <a href="#/knowledge" aria-label="知识库" title="知识库" aria-current={view === 'knowledge' ? 'page' : undefined}>
            <BookOutlined aria-hidden="true" /><span>知识库</span>
          </a>
          <a href="#/sources" aria-label="数据源管理" title="数据源管理" aria-current={view === 'sources' ? 'page' : undefined}>
            <DatabaseOutlined aria-hidden="true" /><span>数据源管理</span>
          </a>
        </nav>
        <button type="button" className="workspace-nav-toggle" aria-controls="workspace-global-nav"
          aria-expanded={!navCollapsed} aria-label={navCollapsed ? '展开工作台导航' : '收起工作台导航'}
          title={navCollapsed ? '展开工作台导航' : '收起工作台导航'}
          onClick={() => {
            localStorage.setItem('workspace-nav-collapsed', String(!navCollapsed))
            setNavCollapsed(!navCollapsed)
          }}>
          {navCollapsed ? <MenuUnfoldOutlined aria-hidden="true" /> : <MenuFoldOutlined aria-hidden="true" />}
          <span>收起导航</span>
        </button>
      </aside>

      <section className="workspace-main" id="workspace-main" tabIndex={-1}>
        <header className="workspace-topbar">
          <div className="workspace-topbar-left">
            <Button
              ref={mobileMenuRef}
              className="conversation-toggle"
              aria-label="打开工作台导航"
              aria-expanded={conversationsOpen}
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setConversationsOpen(value => !value)}
            />
            <span className="workspace-topbar-title">{isConversation ? '对话工作台' : 'AI Agent 工作台'}</span>
          </div>
          <span className="workspace-profile"><UserOutlined aria-hidden="true" />本地工作区</span>
        </header>

        {view === 'tasks' && (
          <div className="workspace-scroll">
            <WorkspaceActivity conversations={conversations} onOpen={selectConversation} />
          </div>
        )}
        {view === 'history' && (
          <div className="workspace-scroll">
            <div className="workspace-history-page">
              <ConversationHistory conversations={conversations} activeId={active.id} onOpen={selectConversation} />
            </div>
          </div>
        )}
        {view === 'capabilities' && (
          <div className="workspace-scroll">
            <Suspense fallback={<p className="workbench-empty" role="status">正在打开 Agent 能力…</p>}>
              <SpecialistDirectory conversationId={active.id} />
            </Suspense>
          </div>
        )}
        {view === 'observability' && (
          <div className="workspace-scroll">
            <Suspense fallback={<p className="workbench-empty" role="status">正在打开运行中心…</p>}>
              <ObservabilityPage
                conversationForRun={runId => conversationsByRun.get(runId)}
                onOpenConversation={selectConversation}
              />
            </Suspense>
          </div>
        )}
        {view === 'knowledge' && (
          <div className="workspace-scroll">
            <Suspense fallback={replyLoading}><KnowledgePage onSelect={startWithDocument} /></Suspense>
          </div>
        )}
        {view === 'sources' && (
          <div className="workspace-scroll">
            <Suspense fallback={replyLoading}><DataSourcesPage /></Suspense>
          </div>
        )}
        {view === 'chat' && emptyConversation && (
          <div className="workspace-scroll workspace-home-scroll">
            <WorkspaceHome
              composer={composer}
              recent={<WorkspaceActivity recent conversations={conversations} onOpen={selectConversation} />}
              onChoose={prompt => {
                setInput(prompt)
                composerRef.current?.focus({ cursor: 'end' })
              }}
            />
          </div>
        )}
        {isConversation && (
          <div ref={dialogRef} className={`workspace-dialog-layout${dockHistory ? ' with-history' : ''}${dockDetails && showDetails ? ' with-execution' : ''}`}>
            {dockHistory && <ConversationHistory compact conversations={conversations} activeId={active.id} onOpen={selectConversation} />}
            {!lg && showHistory && <Drawer title="历史会话" placement="left" open onClose={() => setHistoryOpen(false)}>
              <ConversationHistory compact conversations={conversations} activeId={active.id} onOpen={selectConversation} />
            </Drawer>}
            <section className="chat-main" aria-label="主 Agent 对话工作台">
              <header className="chat-title-bar">
                <div className="chat-title-left">
                  <Tooltip title={showHistory ? '收起历史会话' : '展开历史会话'}>
                    <Button type="text" icon={<HistoryOutlined />} aria-label={showHistory ? '收起历史会话' : '展开历史会话'}
                      aria-expanded={showHistory} onClick={() => setHistoryOpen(!showHistory)} />
                  </Tooltip>
                  <div>
                    <h1 title={heading}>{heading}</h1>
                  </div>
                </div>
                <div className="chat-title-actions">
                  {currentStatus !== 'idle' && <span className={`workspace-state workspace-state-${statusTone}`}>{statusLabel}</span>}
                  {!showDetails && (
                    <Button
                      size="small"
                      icon={<ApartmentOutlined />}
                      aria-label="打开执行详情"
                      aria-expanded={false}
                      onClick={() => { setDetailMessageId(undefined); setDetailsOpen(true) }}
                    >执行详情</Button>
                  )}
                </div>
              </header>

              <div className="chat-transcript">
              <div className="chat-messages" ref={messagesRef} role="region" aria-label="对话消息" tabIndex={0}
                onClickCapture={event => {
                  if ((event.target as HTMLElement).closest('button,summary,a,[role="tab"],[role="combobox"]')) preserveReadingPosition(event.currentTarget)
                }}
                onKeyDownCapture={event => {
                  if ((event.target as HTMLElement).closest('[role="tablist"],summary,[role="combobox"]')) preserveReadingPosition(event.currentTarget)
                  else readingResult.current = false
                }}
                onWheel={() => { readingResult.current = false }}
                onTouchMove={() => { readingResult.current = false }}
                onScroll={event => {
                  const { scrollHeight, clientHeight, scrollTop } = event.currentTarget
                  followLatest.current = scrollHeight - clientHeight - scrollTop <= 48
                  setAwayFromLatest(!followLatest.current)
                }}>
                <div className="chat-message-list" ref={messageListRef}>
                  {messages.map(item => (
                    <article
                      className={`chat-message ${item.role}${item === liveMessage ? ' streaming' : ''}`}
                      key={item.id}
                      ref={item.id === inspectedReply?.id ? inspectedReplyRef : undefined}
                    >
                      <div className={`avatar${item.role === 'assistant' ? ' agent-avatar' : ''}`} aria-hidden="true">
                        {item.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                      </div>
                      <div className="chat-message-content">
                        {item.role === 'assistant' ? (
                          <>
                            <AssistantRunStatus
                              agentRuns={item.agentRuns}
                              stage={item === liveMessage ? run.stage : undefined}
                              running={item === liveMessage && running}
                              onInspect={item.runId && (!showDetails || item.id !== detailReply?.id)
                                ? () => { setDetailMessageId(item.id); setDetailsOpen(true) } : undefined}
                            />
                            {item.error && (item !== taskReply || !failed) && <p className="chat-run-tool-error">{item.error}</p>}
                            <ReplyContent
                              content={withoutRepeatedQuestions(item.content, item.id === taskReply?.id ? pendingInput : undefined)}
                              tools={item.toolResults}
                              onChooseTravel={item.id === taskReply?.id && pendingInput && !running
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
                                  canContinue={item.id === taskReply?.id && !!item.inputRequest && !running}
                                  onContinue={choice => void send('answer', choice)}
                                />
                              </Suspense>
                            )}
                            {item === liveMessage && running && run.assistantText && <span className="cursor" />}
                          </>
                        ) : (
                          <>
                            <div>{item.content}</div>
                            {!!item.documents?.length && <DocumentChips documents={item.documents} onDownload={download}
                              onReuse={canAttach && !uploadingHere ? attachDocument : undefined} />}
                          </>
                        )}
                      </div>
                      {item.role === 'user' && !Number.isNaN(Date.parse(item.createdAt)) && (
                        <time className="workspace-message-time" dateTime={item.createdAt}>{timeLabel.format(new Date(item.createdAt))}</time>
                      )}
                    </article>
                  ))}

                </div>
              </div>
              {awayFromLatest && <Button className="chat-return-latest" aria-label="返回最新消息" size="small" icon={<ArrowDownOutlined />}
                onClick={() => {
                  followLatest.current = true
                  readingResult.current = false
                  messageListRef.current?.style.removeProperty('min-height')
                  setAwayFromLatest(false)
                  const pane = messagesRef.current
                  if (pane) pane.scrollTop = pane.scrollHeight
                }}>返回最新消息</Button>}
              </div>
              {composer}
            </section>
            {dockDetails ? showDetails && (
              <aside className="workbench-panel workspace-execution" aria-label="Agent 执行详情">
                <div className="workbench-panel-header">
                  <h2><ApartmentOutlined aria-hidden="true" />执行详情</h2>
                  <Button type="text" icon={<CloseOutlined />} aria-label="收起执行详情" onClick={() => setDetailsOpen(false)} />
                </div>
                {timeline}
              </aside>
            ) : showDetails && (
              <Drawer title="执行详情" open onClose={() => setDetailsOpen(false)} rootClassName="workspace-execution-drawer">
                {timeline}
              </Drawer>
            )}
          </div>
        )}
      </section>
      {libraryOpen && (
        <Drawer title="从知识库选附件" open onClose={() => setLibraryOpen(false)}>
          <Suspense fallback={replyLoading}><KnowledgePage picker selectedIds={draftDocuments.map(document => document.id)}
            onSelect={attachDocument} /></Suspense>
          {documentError?.conversationId === active.id && <Alert type="error" showIcon title={documentError.message} />}
        </Drawer>
      )}
    </div>
  )
}
