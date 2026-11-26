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
  PlusOutlined,
  ProfileOutlined,
  QuestionCircleFilled,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Grid, Input, Tooltip } from 'antd'
import { type ComponentRef, type MouseEvent, lazy, Suspense, useEffect, useEffectEvent, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react'

import { AssistantRunStatus } from '../components/AssistantRunStatus'
import { ChatAttachmentControls, DocumentChips } from '../components/ChatAttachments'
import { CitedInlineToolResults, ExecutionTimeline } from '../components/ChatRunDetails'
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
  PendingInputSubmission,
  SavedAgentRun,
  RunState,
  ToolRun,
} from '../types/agent'
import type { DocumentSummary } from '../types/workspace'
import './workspace-conversation.css'

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

const workspacePages = {
  chat: { label: '对话工作台', icon: MessageOutlined },
  tasks: { label: '任务工作台', icon: ProfileOutlined },
  history: { label: '历史会话', icon: HistoryOutlined },
  capabilities: { label: 'Agent 能力', icon: ApartmentOutlined },
  observability: { label: '运行中心', icon: MonitorOutlined },
  knowledge: { label: '知识库', icon: BookOutlined },
  sources: { label: '数据源管理', icon: DatabaseOutlined },
} as const

type WorkspaceView = keyof typeof workspacePages
const workspaceViews = Object.keys(workspacePages) as WorkspaceView[]

function workspaceView(hash: string): WorkspaceView {
  const path = hash.split('?', 1)[0]
  return workspaceViews.find(view => path === `#/${view}`) ?? 'chat'
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
      <CitedInlineToolResults tools={tools?.filter(tool => !travelReceiptTools.has(tool.name))} />
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
  runId: string
  message: string
}

interface CreationFailure {
  message?: string
  messageId: string
}

const isRunInProgress = (status: RunState['status']) =>
  status === 'pending' || status === 'running' || status === 'recovering'

function savedTurn(runId: string, afterSequence: number, replyId?: string, prior?: ChatMessage): SavedAgentRun {
  const url = `/api/v1/chat/runs/${encodeURIComponent(runId)}`
  return {
    runId, afterSequence, replyId, eventsUrl: `${url}/events`, cancelUrl: `${url}/cancel`,
    priorAgentRuns: prior?.agentRuns, priorSkills: prior?.skills, followupKey: prior?.followupKey,
  }
}

function readTurn(detail: AgentRunDetail, saved: SavedAgentRun) {
  return readRunSnapshot(detail, saved.afterSequence, {
    ...initialRunState, agentRuns: saved.priorAgentRuns ?? [], skills: saved.priorSkills ?? [],
  })
}

function runReply(turn: SavedAgentRun, state: RunState, existing?: ChatMessage, user?: ChatMessage): ChatMessage {
  const fallback = state.tools.some(tool => tool.status === 'completed')
    ? '已保留本次取得的数据，见下方。'
    : state.status === 'cancelled' ? '本轮已停止。'
      : state.status === 'failed' || state.status === 'recovery_blocked' ? '' : '本轮没有取得可展示的结果。'
  return {
    id: existing?.id ?? turn.replyId ?? (user ? `${user.id}-reply` : `${turn.runId}-reply`),
    role: 'assistant',
    content: state.assistantText || existing?.content || fallback,
    createdAt: existing?.createdAt ?? new Date().toISOString(),
    toolResults: persistableToolResults(state.tools),
    agentRuns: state.agentRuns,
    skills: state.skills,
    runId: turn.runId,
    runStatus: state.status,
    afterSequence: turn.afterSequence,
    throughSequence: Math.max(state.lastSequence, turn.afterSequence),
    error: state.error,
    partial: state.partial,
    inputRequest: state.status === 'waiting_for_input' ? state.humanInput : undefined,
    followupKey: state.agentRuns.some(item => item.agent === 'followup') ? turn.followupKey : undefined,
  }
}

function storeRunReply(conversation: Conversation, turn: SavedAgentRun, state: RunState): Conversation {
  const existing = conversation.messages.filter(message => message.role === 'assistant' && message.runId === turn.runId
    && (message.id === turn.replyId || (message.afterSequence ?? 0) === turn.afterSequence)).at(-1)
  const user = conversation.messages.filter(message => message.role === 'user').at(-1)
  const message = runReply(turn, state, existing, user)
  return {
    ...conversation,
    activeRun: !isRunInProgress(state.status) && conversation.activeRun?.runId === turn.runId
      && conversation.activeRun.afterSequence === turn.afterSequence
      ? undefined : conversation.activeRun,
    messages: existing ? conversation.messages.map(item => item.id === existing.id ? message : item)
      : [...conversation.messages, message],
    updatedAt: new Date().toISOString(),
  }
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState(() => loadActiveConversationId(conversations))
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [starterChoice, setStarterChoice] = useState<string>()
  const [creatingRuns, setCreatingRuns] = useState<string[]>([])
  const [creationFailures, setCreationFailures] = useState<Partial<Record<string, CreationFailure>>>({})
  const [routeHash, setRouteHash] = useState(() => window.location.hash)
  const view = workspaceView(routeHash)
  const [run, dispatch] = useReducer((state: RunState, update: AgentEvent | RunState) =>
    'stage' in update ? update : reduceAgentEvent(state, update), initialRunState)
  const [cancelUrl, setCancelUrl] = useState<string>()
  const [menuOpen, setMenuOpen] = useState(false)
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
  const [historyTarget, setHistoryTarget] = useState<{ messageId: string }>()
  const [verified, setVerified] = useState<AgentRunDetail>()
  const [lookupFailure, setLookupFailure] = useState<string>()
  const [lookupAttempt, setLookupAttempt] = useState(0)
  const [historicDetail, setHistoricDetail] = useState<AgentRunDetail>()
  const [selectedSnapshot, setSelectedSnapshot] = useState<ChatMessage>()
  const [inspectedRunId, setInspectedRunId] = useState<string | undefined>(() =>
    sessionStorage.getItem(`workspace-task:${activeId}`) ?? undefined)
  const [liveConnectionIssue, setConnectionIssue] = useState<ConnectionIssue>()
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
  const navigationRef = useRef<HTMLElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const focusAfterNavigation = useRef<'content' | 'menu' | 'composer' | 'current-link'>(undefined)
  const pageScroll = useRef(new Map<WorkspaceView, number>())
  const conversationScroll = useRef<{ id: string, top: number }>(undefined)
  const runRef = useRef(initialRunState)
  const activeIdRef = useRef(activeId)
  const mountedRef = useRef(false)
  const activeConnectionRef = useRef<ActiveRunConnection | undefined>(undefined)
  const submittingInputs = useRef(new Set<string>())
  const active = conversations.find((item) => item.id === activeId) ?? conversations[0]
  const pendingSubmission = active.pendingInputSubmission
  const draftDocuments = active.draftDocuments ?? []
  const input = drafts[active.id] ?? ''
  const setInput = (value: string) => setDrafts(items => ({ ...items, [active.id]: value }))
  const replies = active.messages.filter(message => message.role === 'assistant')
  const lastMessage = active.messages.at(-1)
  const latestAssistant = lastMessage?.role === 'assistant' ? lastMessage : undefined
  const creatingHere = creatingRuns.includes(active.id)
  const creationFailure = creationFailures[active.id]
  const running = isRunInProgress(run.status) || creatingHere || Boolean(active.activeRun)
  const conversationRunId = active.activeRun?.runId ?? pendingSubmission?.runId
    ?? run.runId ?? (running ? undefined : latestAssistant?.runId)
  const inspectingTask = Boolean(inspectedRunId)
  const inspectingOtherRun = inspectingTask && inspectedRunId !== conversationRunId
  const readingHistory = Boolean(historyTarget)
  // Selecting a receipt does not transfer ownership of the conversation's live connection.
  const readOnly = readingHistory || inspectingOtherRun && (running || !!pendingSubmission || reconnecting)
  const actionRunId = inspectedRunId ?? conversationRunId
  const storedTaskReply = actionRunId ? replies.filter(message => message.runId === actionRunId).at(-1)
    : running ? undefined : latestAssistant
  const snapshotReply = inspectedRunId && selectedSnapshot?.runId === inspectedRunId ? selectedSnapshot : undefined
  const taskReply = snapshotReply ?? storedTaskReply
  const historyIndex = historyTarget ? active.messages.findIndex(message => message.id === historyTarget.messageId) : -1
  const historyMatch = historyIndex >= 0 ? active.messages[historyIndex] : undefined
  const nextHistoryMessage = active.messages[historyIndex + 1]
  const historyReply = historyMatch?.role === 'assistant' ? historyMatch
    : historyMatch && nextHistoryMessage?.role === 'assistant' ? nextHistoryMessage : undefined
  const connectionIssue = liveConnectionIssue?.runId === actionRunId ? liveConnectionIssue : undefined
  const creationError = !running && !inspectingTask && !pendingSubmission ? creationFailure?.message : undefined
  const currentStatus = readingHistory ? replyStatus(historyReply) ?? 'idle'
    : running && !inspectingOtherRun ? (isRunInProgress(run.status) ? run.status : 'pending')
      : run.status !== 'idle' && run.runId === actionRunId ? run.status : replyStatus(taskReply) ?? 'idle'
  const waitingRequest = !readOnly && currentStatus === 'waiting_for_input' ? taskReply?.inputRequest : undefined
  const awaitingFollowup = !!waitingRequest && !!taskReply?.followupKey
  const followupPanels = new Map<string, string>()
  for (const message of active.messages) {
    if (message.runId && message.followupKey) followupPanels.set(message.runId, message.id)
  }
  const awaitingPractice = !!waitingRequest
    && taskReply?.toolResults?.some(tool => tool.name === mathPracticeToolName)
  const lastUser = active.messages.filter(message => message.role === 'user').at(-1)
  const liveId = active.activeRun?.replyId ?? `${lastUser?.id}-reply`
  const currentReplyId = !running && run.runId
    ? replies.filter(reply => reply.runId === run.runId).at(-1)?.id : undefined
  const liveMessage: ChatMessage = { id: liveId, role: 'assistant', createdAt: '', content: run.assistantText,
    agentRuns: run.agentRuns, toolResults: run.tools, runId: run.runId }
  const storedSnapshot = snapshotReply && active.messages.find(message => message.id === snapshotReply.id)
  const transcript = !snapshotReply ? active.messages : storedSnapshot
    ? active.messages.map(message => message.id === snapshotReply.id ? snapshotReply : message)
    : [...active.messages, snapshotReply]
  const messages = !running ? transcript : transcript.some(item => item.id === liveId)
    ? transcript.map(item => item.id === liveId ? liveMessage : item) : [...transcript, liveMessage]
  const visibleMessages = messages.filter(item => !(item.runStatus === 'recovery_blocked'
    && item.afterSequence && !item.content && !item.toolResults?.length))
  const inspectedReply = historyMatch ?? (inspectingTask && (inspectingOtherRun || !running) ? taskReply : undefined)
  const inspectedMessageId = visibleMessages.some(item => item.id === inspectedReply?.id) ? inspectedReply?.id : undefined
  const emptyConversation = active.messages.length === 0 && !run.assistantText && run.tools.length === 0 && !inspectingTask
  const uploadingHere = uploading.includes(active.id)
  const taskUnavailable = inspectingTask && !running && !taskReply
  const canAttach = !readOnly && !running && !pendingSubmission && !waitingRequest && !taskUnavailable && !reconnecting
  const isConversation = view === 'chat' && !emptyConversation
  const { lg, xxl } = Grid.useBreakpoint()
  const showHistory = historyOpen ?? Boolean(lg)
  const dockHistory = lg && showHistory
  const dockDetails = dialogWidth >= (dockHistory ? 1310 : 1080)
  const showDetails = detailsOpen ?? Boolean(xxl && dockDetails)
  const partial = currentStatus === 'completed' && (readingHistory ? historyReply?.partial : taskReply?.partial)
  const [statusLabel, statusTone] = taskStatus(creationError ? 'failed' : currentStatus, partial)
  const pendingInput = pendingSubmission || awaitingFollowup ? undefined : waitingRequest
  const failed = currentStatus === 'failed' || currentStatus === 'recovery_blocked'
  const firstReplyIndex = actionRunId ? active.messages.findIndex(message => message.runId === actionRunId) : -1
  const taskQuestion = firstReplyIndex >= 0
    ? active.messages.slice(0, firstReplyIndex).filter(message => message.role === 'user').at(-1) : lastUser
  const heading = (inspectingTask ? taskTitle : undefined) ?? (readingHistory ? active.title : undefined)
    ?? (taskQuestion?.documents?.length ? '资料问答' : taskQuestion?.content.slice(0, 120)) ?? active.title
  const canResume = !readOnly && !running && failed && !pendingSubmission && verified?.id === actionRunId && !!verified?.checkpoint
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

  const updateRoute = (nextHash: string) => {
    if (workspaceView(nextHash) !== view) {
      const page = mainRef.current?.querySelector<HTMLElement>('.workspace-scroll')
      if (page) pageScroll.current.set(view, page.scrollTop)
      const transcript = messagesRef.current
      if (view === 'chat' && transcript) {
        conversationScroll.current = followLatest.current ? undefined : { id: active.id, top: transcript.scrollTop }
      }
      focusAfterNavigation.current ??= 'content'
      setMenuOpen(false)
    }
    setRouteHash(nextHash)
  }

  const followRoute = useEffectEvent(() => updateRoute(window.location.hash))
  useEffect(() => {
    const onLocationChange = () => followRoute()
    window.addEventListener('hashchange', onLocationChange)
    return () => window.removeEventListener('hashchange', onLocationChange)
  }, [])

  useLayoutEffect(() => {
    const pane = mainRef.current?.querySelector<HTMLElement>('.workspace-scroll')
    const top = pageScroll.current.get(view) ?? 0
    if (!pane || !top) return
    pane.scrollTop = top
    if (pane.scrollTop >= top) return

    // A lazy page may initially render only a loading message. Wait for its content unless the user intervenes.
    const stop = () => {
      observer.disconnect()
      for (const event of ['wheel', 'pointerdown', 'keydown']) pane.removeEventListener(event, stop)
    }
    const observer = new MutationObserver(() => {
      if (pane.scrollHeight - pane.clientHeight < top) return
      pane.scrollTop = top
      stop()
    })
    observer.observe(pane, { childList: true, subtree: true })
    for (const event of ['wheel', 'pointerdown', 'keydown']) pane.addEventListener(event, stop, { passive: true })
    return stop
  }, [view])

  const closeMenu = (focus: 'menu' | 'content' | 'composer') => {
    focusAfterNavigation.current = focus
    setMenuOpen(false)
  }

  const activatePage = (event: MouseEvent<HTMLAnchorElement>, page: WorkspaceView) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    if (page === view) {
      event.preventDefault()
      if (menuOpen) closeMenu('content')
    }
  }

  useLayoutEffect(() => {
    const currentLink = navigationRef.current?.querySelector<HTMLElement>('a[aria-current="page"]')
    if (menuOpen) {
      currentLink?.focus()
      // A mobile rail can still be hidden before its opening styles take effect.
      if (document.activeElement === currentLink) return
      const frame = requestAnimationFrame(() => currentLink?.focus())
      return () => cancelAnimationFrame(frame)
    }
    const destination = focusAfterNavigation.current
    focusAfterNavigation.current = undefined
    if (destination === 'menu') mobileMenuRef.current?.focus({ preventScroll: true })
    else if (destination === 'current-link') currentLink?.focus({ preventScroll: true })
    else if (destination === 'composer' && composerRef.current) composerRef.current.focus({ preventScroll: true })
    else if (destination) mainRef.current?.focus({ preventScroll: true })
  }, [menuOpen, view, active.id])

  useEffect(() => {
    if (!menuOpen) return
    const mobile = window.matchMedia('(max-width: 840px)')
    const closeOnDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) return
      focusAfterNavigation.current = 'current-link'
      setMenuOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        focusAfterNavigation.current = 'menu'
        setMenuOpen(false)
        return
      }
      if (event.key !== 'Tab') return
      const controls = navigationRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([hidden])')
      const first = controls?.[0]
      const last = controls?.[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    mobile.addEventListener('change', closeOnDesktop)
    return () => {
      window.removeEventListener('keydown', onKey)
      mobile.removeEventListener('change', closeOnDesktop)
    }
  }, [menuOpen])

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

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
    const saved = conversationScroll.current
    conversationScroll.current = undefined
    messageListRef.current?.style.removeProperty('min-height')
    readingResult.current = Boolean(inspected)
    followLatest.current = !inspected && saved?.id !== active.id
    const align = () => {
      if (inspected && readingResult.current) {
        pane.scrollTop += inspected.getBoundingClientRect().top - pane.getBoundingClientRect().top - 18
      }
    }
    if (!inspected) {
      pane.scrollTop = saved?.id === active.id ? saved.top : pane.scrollHeight
      return
    }
    align()
    inspected.focus({ preventScroll: true })
    const list = messageListRef.current
    if (!list) return
    const observer = new ResizeObserver(align)
    observer.observe(list)
    return () => observer.disconnect()
  }, [isConversation, active.id, inspectedMessageId, historyTarget])

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
      return storeRunReply(conversation, connection, state)
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
          runId: connection.runId,
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

  const reconcileInputSubmission = async (
    submission: PendingInputSubmission, user: ChatMessage, cause?: unknown, draft?: string,
  ) => {
    const detail = await getAgentRun(submission.runId).catch(() => undefined)
    const events = detail?.id === submission.runId ? detail.events : undefined
    const submitted = events?.find(item => (item.event_type ?? item.type) === 'input.submitted'
      && item.payload.request_id === submission.requestId)
    const prior = replies.filter(reply => reply.runId === submission.runId
      && reply.inputRequest?.requestId === submission.requestId).at(-1)
    const isCurrent = mountedRef.current && activeIdRef.current === active.id

    if (detail && submitted && submitted.payload.answer === user.content
      && (submitted.payload.action ?? 'answer') === submission.action) {
      const turn = savedTurn(submission.runId, submitted.sequence, `${user.id}-reply`, prior)
      const state = readTurn(detail, turn)
      setActiveConversation(conversation => {
        if (conversation.pendingInputSubmission?.messageId !== user.id) return conversation
        const settled = { ...conversation, pendingInputSubmission: undefined }
        return isRunInProgress(state.status) ? { ...settled, activeRun: turn }
          : storeRunReply(settled, turn, state)
      })
      if (!isCurrent) return
      if (isRunInProgress(state.status)) {
        resetRun('pending', turn)
        void attachRun(turn, active.id)
      } else {
        setVerified(detail)
        setCancelUrl(undefined)
        runRef.current = state
        dispatch(state)
      }
      return
    }

    const status = detail?.status.toUpperCase()
    const waiting = events?.filter(item => (item.event_type ?? item.type) === 'run.waiting_for_input').at(-1)
    const stillWaiting = status === 'WAITING_FOR_INPUT' && waiting != null
    const canRestoreDraft = stillWaiting && waiting.payload.request_id === submission.requestId
    const httpStatus = cause instanceof Error && 'status' in cause ? cause.status : undefined
    const rejected = typeof httpStatus === 'number' && httpStatus >= 400 && httpStatus < 500 && httpStatus !== 409
    const changed = Boolean(submitted || (stillWaiting && !canRestoreDraft) || (events?.length && status
      && ['COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_BLOCKED'].includes(status)))
    if (!rejected && !changed && !canRestoreDraft) {
      if (isCurrent) resetRun('idle')
      return
    }

    setActiveConversation(conversation => conversation.pendingInputSubmission?.messageId === user.id ? {
      ...conversation, pendingInputSubmission: undefined,
      messages: conversation.messages.filter(message => message.id !== user.id),
    } : conversation)
    if (!changed) {
      setInput(draft ?? (submission.action === 'answer' && !prior?.followupKey ? user.content : ''))
    }
    if (!isCurrent) return
    setInspectedRunId(current => current ?? submission.runId)
    resetRun('idle')
    showDocumentError(changed ? '补充请求已更新，请查看本次任务。'
      : cause instanceof Error ? cause.message : '补充尚未保存，请重新提交。')
  }

  const recheckInput = async () => {
    if (!pendingSubmission || submittingInputs.current.has(active.id)) return
    const user = active.messages.find(message => message.id === pendingSubmission.messageId)
    if (!user) return
    submittingInputs.current.add(active.id)
    setCancelUrl(undefined)
    runRef.current = { ...initialRunState, status: 'pending', stage: '正在核对补充' }
    dispatch(runRef.current)
    try {
      await reconcileInputSubmission(pendingSubmission, user)
    } finally {
      submittingInputs.current.delete(active.id)
    }
  }

  const restoreActiveRun = useEffectEvent(() => {
    if (pendingSubmission) {
      void recheckInput()
      return
    }
    const saved = active.activeRun
    if (creatingHere || activeConnectionRef.current || !saved) return
    resetRun('pending', saved)
    void attachRun(saved, active.id)
  })

  useEffect(() => {
    saveActiveConversationId(activeId)
    const restoreTimer = window.setTimeout(restoreActiveRun, 0)
    return () => {
      window.clearTimeout(restoreTimer)
      // Selection already detached the previous stream. Leave a newly attached stream alone.
      if (activeIdRef.current !== activeId) return
      abortRef.current?.abort()
      abortRef.current = undefined
      activeConnectionRef.current = undefined
    }
  }, [activeId])

  const reconnectActiveRun = async (retry = false) => {
    if (readOnly || reconnecting || (retry && (!canResume || !actionRunId))) return
    let connection = activeConnectionRef.current
    if (retry && actionRunId) {
      const reply = taskReply?.runId === actionRunId ? taskReply : undefined
      const turn = savedTurn(actionRunId, reply?.afterSequence ?? 0, reply?.id, reply)
      connection = { ...turn, conversationId: active.id, lastEventId: turn.afterSequence }
      activeConnectionRef.current = connection
    }
    if (!connection || connection.conversationId !== active.id || connection.runId !== actionRunId) return
    setReconnecting(true)
    setConnectionIssue(undefined)
    try {
      if (retry) {
        const state = readTurn(verified!, connection)
        await resumeAgentRun(connection.runId)
        if (activeConnectionRef.current !== connection) return
        connection = { ...connection, lastEventId: Math.max(state.lastSequence, connection.lastEventId) }
        activeConnectionRef.current = connection
        const saved = { ...connection }
        setActiveConversation(conversation => ({ ...conversation, activeRun: saved }))
        runRef.current = { ...state, status: 'recovering', stage: '正在恢复任务', error: undefined }
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
          const turn = connection
          const state = readTurn(detail, turn)
          runRef.current = state
          dispatch(state)
          setActiveConversation(conversation => storeRunReply(conversation, turn, state))
          setReconnecting(false)
          return
        }
      }
      setConnectionIssue({
        runId: connection.runId,
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
    if (!content || readOnly || running || isRunInProgress(runRef.current.status) || pendingSubmission
      || submittingInputs.current.has(active.id) || reconnecting || uploadingHere || taskUnavailable) return

    followLatest.current = true
    readingResult.current = false
    messageListRef.current?.style.removeProperty('min-height')
    setAwayFromLatest(false)
    setDetailMessageId(undefined)
    setTaskTitle(undefined)
    setStarterChoice(undefined)
    if (creationFailure) setCreationFailures(failures => {
      const remaining = { ...failures }
      delete remaining[active.id]
      return remaining
    })
    setInput('')
    setInspectedRunId(undefined)
    setConnectionIssue(undefined)
    setCancelUrl(undefined)
    setDocumentError(previous => previous?.conversationId === active.id ? undefined : previous)
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
      ...(documents.length ? { documents } : {}),
    }
    // Replace a rejected create attempt when the user retries it.
    const history = [...active.messages.filter(message => message.id !== creationFailure?.messageId), userMessage]
    const request = waitingRequest
    const previousRunId = taskReply?.runId
    const submission = request && previousRunId
      ? { runId: previousRunId, requestId: request.requestId, messageId: userMessage.id, action }
      : undefined
    const prior = submission ? { priorAgentRuns: taskReply?.agentRuns, priorSkills: taskReply?.skills } : undefined
    if (submission) submittingInputs.current.add(active.id)
    else setCreatingRuns(ids => [...ids, active.id])
    setActiveConversation((conversation) => ({
      ...conversation,
      title: history.length === 1 ? content.slice(0, 28) : conversation.title,
      messages: history,
      pendingInputSubmission: submission,
      updatedAt: new Date().toISOString(),
    }))
    resetRun('pending', prior)

    try {
      const followupKey = request ? taskReply?.followupKey : undefined
      const access: [string?] = followupKey ? [followupKey] : []
      const created = submission
        ? await submitAgentInput(submission.runId, submission.requestId, content, action, ...access)
        : await createAgentRun(history, active.id)
      if (submission && created.status.toUpperCase() === 'RECOVERY_BLOCKED') {
        await reconcileInputSubmission(submission, userMessage)
        return
      }
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
        pendingInputSubmission: undefined,
        ...(documents.length ? { draftDocuments: [] } : {}),
      }))
      if (mountedRef.current && activeIdRef.current === active.id) {
        resetRun('pending', saved)
        void attachRun(saved, active.id)
      }
    } catch (cause) {
      if (submission) {
        await reconcileInputSubmission(submission, userMessage, cause, input)
        return
      }
      // Keep the failed attempt in history so its draft can be reopened after switching away.
      setInput(input)
      setCreationFailures(failures => ({ ...failures, [active.id]: {
        messageId: userMessage.id,
        message: cause instanceof Error ? cause.message : '创建 Agent 运行失败，请重试。',
      } }))
      if (mountedRef.current && activeIdRef.current === active.id) resetRun('idle')
    } finally {
      if (submission) submittingInputs.current.delete(active.id)
      else setCreatingRuns(ids => ids.filter(id => id !== active.id))
    }
  }

  const stop = async () => {
    const connection = activeConnectionRef.current
    if (readOnly || !running || !connection || connection.conversationId !== active.id
      || connection.runId !== actionRunId || connection.cancelUrl !== cancelUrl) return
    try {
      const receipt = await cancelAgentRun(connection.cancelUrl)
      // A completion can win the race with a stop. Keep its actual terminal event.
      if (receipt.status !== 'cancelled') return
    } catch (cause) {
      if (activeConnectionRef.current === connection) {
        setConnectionIssue({ runId: connection.runId, message: (cause as Error).message })
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

  const selectConversation = (id: string, runId?: string, title?: string, messageId?: string) => {
    conversationScroll.current = undefined
    if (!lg) setHistoryOpen(false)
    setTaskTitle(title)
    if (runId !== inspectedRunId || id !== active.id) setSelectedSnapshot(undefined)
    setHistoryTarget(messageId ? { messageId } : undefined)
    setDetailMessageId(undefined)
    setAwayFromLatest(false)
    setLibraryOpen(false)
    setStarterChoice(undefined)
    setInspectedRunId(runId)
    if (view !== 'chat') {
      window.location.hash = '#/chat'
      updateRoute('#/chat')
    }
    if (activeIdRef.current === id) return
    activeIdRef.current = id
    abortRef.current?.abort()
    abortRef.current = undefined
    setActiveId(id)
    setCancelUrl(undefined)
    setConnectionIssue(undefined)
    setReconnecting(false)
    activeConnectionRef.current = undefined
    resetRun('idle')
  }

  const openHistoryConversation = (id: string, messageId?: string) => selectConversation(id, undefined, undefined, messageId)
  const returnToConversation = () => selectConversation(active.id)
  const returnToLive = (
    <Button size="small" onClick={returnToConversation}>{running ? '返回当前任务' : '返回当前对话'}</Button>
  )

  const startNewTask = waitingRequest && !pendingSubmission && !reconnecting && (
    <Button type="link" size="small" onClick={newConversation}>开始新任务</Button>
  )
  const composer = readOnly || taskUnavailable ? (
    <div className="chat-composer-wrap">
      <div className="workspace-receipt-notice">
        <span role="status">{readingHistory ? '正在查看历史搜索结果。'
          : running || pendingSubmission || reconnecting ? '正在查看所选任务。返回当前对话可操作正在处理的任务。'
            : lookupFailure === actionRunId || verified?.id === actionRunId ? '暂时无法读取所选任务。' : '正在读取所选任务…'}</span>
        {taskUnavailable && (lookupFailure === actionRunId || verified?.id === actionRunId) && <Button size="small" onClick={() => {
          setLookupFailure(undefined)
          setLookupAttempt(count => count + 1)
        }}>重试</Button>}
        {returnToLive}
      </div>
    </div>
  ) : awaitingFollowup && !pendingSubmission && !running && !connectionIssue && !failed ? (
    <div className="chat-composer-wrap">
      <small>通过上方的复诊资料区继续本次整理。</small>{startNewTask}{inspectingOtherRun && returnToLive}
    </div>
  ) : (
    <div className="chat-composer-wrap">
      {pendingSubmission && !running && (
        <Alert type="warning" showIcon title="暂时无法确认补充是否已保存"
          description="核对提交结果后可以继续本次任务。"
          action={<Button size="small" onClick={() => void recheckInput()}>重新核对</Button>} />
      )}
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
          description={canResume ? (taskReply?.afterSequence
            ? '补充已保存，可从本次任务的检查点继续。' : '可从本次任务的检查点继续。') : undefined}
          action={canResume ? (
            <Button size="small" loading={reconnecting} onClick={() => void reconnectActiveRun(true)}>
              恢复任务
            </Button>
          ) : lookupFailure === actionRunId && lookupFailure ? (
            <Button size="small" onClick={() => { setLookupFailure(undefined); setLookupAttempt(count => count + 1) }}>
              重查恢复状态
            </Button>
          ) : undefined}
        />
      ) : null}
      {documentError?.conversationId === active.id && (
        <Alert type="error" showIcon title={documentError.message} closable onClose={() => setDocumentError(undefined)} />
      )}
      {creationFailure && creationError && (
        <Alert type="error" showIcon title={creationError} closable onClose={() => {
          setCreationFailures(failures => ({ ...failures, [active.id]: { messageId: creationFailure.messageId } }))
        }} />
      )}
      {starterChoice && (
        <Alert type="warning" title="要用示例替换当前草稿吗？"
          description={draftDocuments.length ? '替换会从这次草稿移除附件，知识库中的文件仍然保留。' : undefined} action={(
          <>
            <Button size="small" onClick={() => setStarterChoice(undefined)}>保留草稿</Button>
            <Button size="small" onClick={() => {
              setInput(starterChoice)
              if (draftDocuments.length) setActiveConversation(conversation => ({ ...conversation, draftDocuments: [] }))
              setStarterChoice(undefined)
              composerRef.current?.focus({ cursor: 'end' })
            }}>替换草稿</Button>
          </>
        )} />
      )}
      <div className={`workspace-input-surface${pendingInput ? ' awaiting-input' : ''}`}>
        {pendingInput && (
          <div className="workspace-input-request">
            <h2><QuestionCircleFilled aria-hidden="true" />{awaitingPractice ? '等待作答' : '等待补充'}</h2>
            {pendingInput.questions.length > 0 && (
              <div id="workspace-pending-questions" className="workspace-pending-questions">
                {pendingInput.questions.length === 1 ? <p>{pendingInput.questions[0]}</p> : (
                  <ul>{pendingInput.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
                )}
              </div>
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
            aria-describedby={pendingInput?.questions.length ? 'workspace-pending-questions' : undefined}
            value={input}
            disabled={running || Boolean(pendingSubmission) || awaitingFollowup || reconnecting}
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
                disabled={(!input.trim() && (!canAttach || !draftDocuments.length))
                  || Boolean(pendingSubmission) || awaitingFollowup || uploadingHere || reconnecting || taskUnavailable}
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
            {startNewTask}
            {inspectingOtherRun && returnToLive}
          </small>
        )}
      </div>
    </div>
  )

  const detailReply = detailMessageId ? visibleMessages.find(message => message.id === detailMessageId)
    : readingHistory ? historyReply : taskReply
  const liveDetails = running && (detailMessageId ? detailMessageId === liveId : !inspectingOtherRun && !readingHistory)
  const recordedTools = liveDetails || (currentReplyId && detailReply?.id === currentReplyId) ? run.tools : detailReply?.toolResults
  const detailStatus = replyStatus(detailReply)
  const statsRunId = view === 'chat' && showDetails && !liveDetails ? detailReply?.runId : undefined
  const lookupId = isConversation && !readingHistory
    && (inspectingOtherRun || !running && !pendingSubmission && (inspectingTask || failed || run.status === 'idle'))
    ? actionRunId : undefined
  const syncStoredReply = useEffectEvent((detail: AgentRunDetail) => {
    if (detail.id !== actionRunId || !detail.events?.length) return
    const submitted = detail.events.filter(item => (item.event_type ?? item.type) === 'input.submitted').at(-1)
    const afterSequence = submitted?.sequence ?? 0
    const runReplies = replies.filter(reply => reply.runId === detail.id)
    const previous = runReplies.filter(reply => (reply.afterSequence ?? 0) === afterSequence).at(-1)
    const question = submitted && runReplies.filter(reply =>
      reply.inputRequest?.requestId === submitted.payload.request_id).at(-1)
    const next = question && active.messages[active.messages.indexOf(question) + 1]
    const existingAnswer = next?.role === 'user' && next.content === submitted?.payload.answer ? next : undefined
    const recoveredAnswer: ChatMessage | undefined = submitted && !previous && !existingAnswer
      && typeof submitted.payload.answer === 'string' ? {
        id: `${detail.id}-input-${submitted.sequence}`, role: 'user',
        content: submitted.payload.answer, createdAt: submitted.created_at,
      } : undefined
    const user = existingAnswer ?? recoveredAnswer
    const replyId = previous?.id ?? (user ? `${user.id}-reply` : `${detail.id}-${afterSequence}-reply`)
    const saved = savedTurn(detail.id, afterSequence, replyId, question ?? previous)
    const state = readTurn(detail, saved)
    const upToDate = previous && previous.runStatus === state.status && previous.error === state.error
      && previous.inputRequest?.requestId === state.humanInput?.requestId && !!previous.partial === !!state.partial
      && (previous.throughSequence ?? state.lastSequence) >= state.lastSequence
    if (readOnly || inspectingOtherRun && !previous && !isRunInProgress(state.status)) {
      // Keep the selected receipt separate while another turn owns the live connection.
      setSelectedSnapshot(upToDate ? undefined : runReply(saved, state, previous))
      return
    }
    setSelectedSnapshot(undefined)
    if (pendingSubmission || running || reconnecting || isRunInProgress(runRef.current.status)) return
    const withAnswer = (conversation: Conversation) => recoveredAnswer
      && !conversation.messages.some(message => message.id === recoveredAnswer.id)
      ? { ...conversation, messages: [...conversation.messages, recoveredAnswer] } : conversation
    if (isRunInProgress(state.status)) {
      if (!inspectingTask && runReplies.at(-1) !== latestAssistant) return
      setActiveConversation(conversation => ({ ...withAnswer(conversation), activeRun: saved }))
      resetRun('pending', saved)
      void attachRun(saved, active.id)
      return
    }
    if (upToDate) return
    setActiveConversation(conversation => storeRunReply(withAnswer(conversation), saved, state))
    if (runRef.current.runId === detail.id && runRef.current.status !== state.status) {
      runRef.current = state
      dispatch(state)
    }
  })
  useEffect(() => {
    if (!lookupId) return
    const request = new AbortController()
    void getAgentRun(lookupId, request.signal).then(detail => {
      if (request.signal.aborted || activeIdRef.current !== active.id || detail?.id !== lookupId) return
      setVerified(detail)
      setLookupFailure(undefined)
      syncStoredReply(detail)
    }).catch(() => {
      if (!request.signal.aborted) setLookupFailure(lookupId)
    })
    return () => request.abort()
  }, [lookupId, readOnly, active.id, view, lookupAttempt])
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
  const replyIndex = detailReply ? active.messages.findIndex(message => message.id === detailReply.id) : -1
  const relatedTurn = liveDetails ? lastUser : replyIndex >= 0
    ? active.messages.slice(0, replyIndex).reverse().find(message => message.role === 'user') : undefined
  const timeline = (
    <ExecutionTimeline
      key={liveDetails ? liveId : detailReply?.id}
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
    <div className={`chat-workspace${navCollapsed ? ' workspace-nav-compact' : ''}${isConversation ? ' workspace-in-conversation' : ''}${menuOpen ? ' conversations-open' : ''}`}>
      <a className="workspace-skip" tabIndex={menuOpen ? -1 : undefined} href="#workspace-main" onClick={event => {
        event.preventDefault()
        mainRef.current?.focus({ preventScroll: true })
      }}>跳到当前页面</a>
      <div className="chat-panel-backdrop" aria-hidden="true" onClick={() => closeMenu('menu')} />
      <aside ref={navigationRef} id="workspace-sidebar" className="chat-conversations" aria-label="工作台菜单"
        role={menuOpen ? 'dialog' : undefined} aria-modal={menuOpen || undefined}>
        <div className="workspace-brand">
          <img src={`${import.meta.env.BASE_URL}workbench-mark.webp`} width={36} height={36} alt="" />
          <strong>AI Agent 工作台</strong>
          <button type="button" className="workspace-menu-close" aria-label="关闭工作台菜单" hidden={!menuOpen}
            onClick={() => closeMenu('menu')}><CloseOutlined aria-hidden="true" /></button>
        </div>
        <nav id="workspace-global-nav" className="workspace-actions" aria-label="工作台导航">
          <button type="button" className="new-conversation" aria-label="新建对话" title="新建对话" onClick={() => {
            if (view !== 'chat') {
              focusAfterNavigation.current = 'composer'
            } else if (menuOpen) {
              closeMenu('composer')
            } else {
              requestAnimationFrame(() => composerRef.current?.focus({ preventScroll: true }))
            }
            newConversation()
          }}>
            <PlusOutlined aria-hidden="true" /><span>新建对话</span>
          </button>
          {workspaceViews.map(page => {
            const { label, icon: Icon } = workspacePages[page]
            const href = page === view && routeHash.split('?', 1)[0] === `#/${page}` ? routeHash : `#/${page}`
            return (
              <a key={page} href={href} aria-label={label} title={label} aria-current={view === page ? 'page' : undefined}
                onClick={event => activatePage(event, page)}>
                <Icon aria-hidden="true" /><span>{label}</span>
              </a>
            )
          })}
        </nav>
        <button type="button" className="workspace-nav-toggle" aria-controls="workspace-global-nav" hidden={menuOpen}
          aria-expanded={!navCollapsed} aria-label={navCollapsed ? '展开工作台导航' : '收起工作台导航'}
          title={navCollapsed ? '展开工作台导航' : '收起工作台导航'}
          onClick={() => {
            localStorage.setItem('workspace-nav-collapsed', String(!navCollapsed))
            setNavCollapsed(!navCollapsed)
          }}>
          {navCollapsed ? <MenuUnfoldOutlined aria-hidden="true" /> : <MenuFoldOutlined aria-hidden="true" />}
          <span>{navCollapsed ? '展开导航' : '收起导航'}</span>
        </button>
      </aside>

      <section ref={mainRef} className="workspace-main" id="workspace-main" aria-label={workspacePages[view].label} tabIndex={-1} inert={menuOpen}>
        <header className="workspace-topbar">
          <div className="workspace-topbar-left">
            <Button
              ref={mobileMenuRef}
              className="conversation-toggle"
              aria-label="打开工作台菜单"
              aria-controls="workspace-sidebar"
              aria-expanded={menuOpen}
              aria-haspopup="dialog"
              type="text"
              icon={<MenuOutlined aria-hidden="true" />}
              onClick={() => setMenuOpen(true)}
            />
            <span className="workspace-topbar-title">{workspacePages[view].label}</span>
          </div>
          <span className="workspace-profile">本地工作区</span>
        </header>

        {view === 'tasks' && (
          <div className="workspace-scroll">
            <WorkspaceActivity conversations={conversations} onOpen={selectConversation} />
          </div>
        )}
        {view === 'history' && (
          <div className="workspace-scroll">
            <div className="workspace-history-page">
              <ConversationHistory conversations={conversations} activeId={active.id} onOpen={openHistoryConversation} />
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
            <Suspense fallback={replyLoading}><KnowledgePage onSelect={startWithDocument} onDelete={document => {
              setConversations(items => items.map(conversation => conversation.draftDocuments?.some(item => item.id === document.id)
                ? { ...conversation, draftDocuments: conversation.draftDocuments.filter(item => item.id !== document.id) }
                : conversation))
            }} /></Suspense>
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
                if (draftDocuments.length || (input.trim() && input !== prompt)) {
                  setStarterChoice(prompt)
                  return
                }
                setInput(prompt)
                setStarterChoice(undefined)
                composerRef.current?.focus({ cursor: 'end' })
              }}
            />
          </div>
        )}
        {isConversation && (
          <div ref={dialogRef} className={`workspace-dialog-layout${dockHistory ? ' with-history' : ''}${dockDetails && showDetails ? ' with-execution' : ''}`}>
            {dockHistory && <ConversationHistory compact conversations={conversations} activeId={active.id} onOpen={openHistoryConversation} />}
            {!lg && showHistory && <Drawer title="历史会话" placement="left" open onClose={() => setHistoryOpen(false)}>
              <ConversationHistory compact conversations={conversations} activeId={active.id} onOpen={openHistoryConversation} />
            </Drawer>}
            <section className="chat-main" aria-label="主 Agent 对话工作台">
              <header className="chat-title-bar">
                <div className="chat-title-left">
                  <Tooltip title={showHistory ? '收起历史会话' : '展开历史会话'}>
                    <Button type="text" icon={<HistoryOutlined />} aria-label={showHistory ? '收起历史会话' : '展开历史会话'}
                      aria-expanded={showHistory} onClick={() => setHistoryOpen(!showHistory)} />
                  </Tooltip>
                  <div className="conversation-heading">
                    <p>主 Agent 对话工作台</p>
                    <h1 title={heading}>{heading}</h1>
                  </div>
                </div>
                <div className="chat-title-actions">
                  {(currentStatus !== 'idle' || creationError) && <span className={`workspace-state workspace-state-${statusTone}`}>{statusLabel}</span>}
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
                  {visibleMessages.map(item => (
                    <article
                      className={`chat-message ${item.role}${item === liveMessage ? ' streaming' : ''}`}
                      key={item.id}
                      ref={item.id === inspectedMessageId ? inspectedReplyRef : undefined}
                      aria-current={item.id === inspectedMessageId ? 'true' : undefined}
                      tabIndex={item.id === inspectedMessageId ? -1 : undefined}
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
                            {item.error && (readOnly || item !== taskReply || !failed) && <p className="chat-run-tool-error">{item.error}</p>}
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
                            {readOnly && item.id === inspectedMessageId && !!item.inputRequest?.questions.length
                              && withoutRepeatedQuestions(item.content, item.inputRequest) === item.content && (
                                <ul>{item.inputRequest.questions.filter(question => !item.content.includes(question))
                                  .map((question, index) => <li key={index}>{question}</li>)}</ul>
                              )}
                            {item.runId && item.followupKey && followupPanels.get(item.runId) === item.id && (
                              <Suspense fallback={replyLoading}>
                                <FollowupPanel
                                  runId={item.runId} access={item.followupKey}
                                  canContinue={item.id === taskReply?.id && awaitingFollowup && !pendingSubmission && !running && !readOnly}
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
              {awayFromLatest && !readOnly && <Button className="chat-return-latest" aria-label="返回最新消息" size="small" icon={<ArrowDownOutlined />}
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
