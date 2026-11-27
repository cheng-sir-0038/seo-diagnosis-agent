import {
  CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, HistoryOutlined, MessageOutlined,
  ProfileOutlined, ReloadOutlined, RightOutlined, SearchOutlined, SyncOutlined,
} from '@ant-design/icons'
import { Button, Input, Segmented } from 'antd'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'

import { listWorkspaceTasks } from '../services/agentApi'
import { specialistPresentation } from '../specialists'
import { conversationStatus, taskStatus } from '../state/taskStatus'
import type { ChatMessage, Conversation, ToolRun, WorkspaceTaskRow } from '../types/agent'
import { sanitizeAssistantContent } from './assistantContentSanitizer'
import {
  isCardResultData, isChartResultData, isPageMetadataData, isPagePerformanceData, isPresentationBase,
  isSeoSearchOverviewResultData, isTableResultData, isTreeResultData, resolveLocalizedText, unwrapToolResult,
} from './renderers/contracts'

const timeFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const dayFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' })
const WorkspaceTrend = lazy(() => import('./WorkspaceTrend'))

const statusIcons: Partial<Record<string, typeof SyncOutlined>> = {
  running: SyncOutlined,
  waiting: ClockCircleOutlined,
  success: CheckCircleOutlined,
  error: ExclamationCircleOutlined,
}

function TaskStatus({ status, showIcon = false }: { status?: string; showIcon?: boolean }) {
  if (!status) return null
  const [label, tone] = taskStatus(status)
  const Icon = showIcon ? statusIcons[tone] : undefined
  return <span className={`workspace-state workspace-state-${tone}`}>{Icon && <Icon aria-hidden="true" />}{label}</span>
}

function formatActivityTime(value: string) {
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? '' : timeFormat.format(time)
}

const compactText = (value: string) => value.replace(/\s+/g, ' ').trim()

function* visibleResultText(tool: ToolRun): Generator<string> {
  if (tool.status !== 'completed' || !tool.render_hint) return
  const data = unwrapToolResult(tool.result)
  if (isPresentationBase(data)) {
    yield resolveLocalizedText(data.title)
    yield resolveLocalizedText(data.description)
  }
  // Index only known display fields. Never flatten the tool receipt or any nested payload.
  if (tool.render_hint === 'cards' && isCardResultData(data)) {
    for (const item of data.items) {
      yield item.title
      if (item.summary) yield item.summary
      for (const entry of item.metadata ?? []) yield `${entry.label} ${entry.value}`
    }
  } else if (tool.render_hint === 'table' && isTableResultData(data)) {
    for (const row of data.rows) {
      const cells = data.columns.flatMap(column => {
        const value = row[column.key]
        if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return []
        if (column.data_type === 'url' || column.formatter === 'compact_url') return []
        return column.value_map?.[String(value)] ?? String(value)
      })
      yield cells.join(' ')
    }
  } else if (tool.render_hint === 'tree' && isTreeResultData(data)) {
    const nodes = [...data.nodes]
    while (nodes.length) {
      const node = nodes.pop()!
      yield node.title
      nodes.push(...(node.children ?? []))
    }
  } else if (tool.render_hint === 'chart') {
    if (isPageMetadataData(data)) {
      for (const check of data.checks) yield [check.label, check.value].filter(Boolean).join(' ')
      yield* data.headings
    } else if (isPagePerformanceData(data) || isSeoSearchOverviewResultData(data)) {
      for (const metric of data.metrics) yield `${metric.label} ${'display_value' in metric ? metric.display_value : metric.value}`
    } else if (isChartResultData(data)) {
      yield* data.labels
      for (const series of data.datasets) yield series.label
    }
  }
}

function* messageSearchText(message: ChatMessage): Generator<string> {
  yield message.role === 'assistant'
    ? sanitizeAssistantContent(message.content).content.replace(/!?\[([^\]]*)\]\([^\n]*?\)/g, '$1')
    : message.content
  if (message.role === 'assistant') {
    for (const tool of message.toolResults ?? []) yield* visibleResultText(tool)
  }
}

function historyMatch(conversation: Conversation, query: string) {
  if (!query) return { conversation }
  for (let index = conversation.messages.length - 1; index >= 0; index--) {
    const message = conversation.messages[index]
    if (message.role === 'assistant' && message.runStatus === 'recovery_blocked'
      && message.afterSequence && !message.content && !message.toolResults?.length) continue
    for (const field of messageSearchText(message)) {
      const text = compactText(field)
      const match = text.toLocaleLowerCase().indexOf(query)
      if (match < 0) continue
      const start = Math.max(0, match - 18)
      const end = Math.min(text.length, match + query.length + 55)
      return { conversation, messageId: message.id, snippet: `${start ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}` }
    }
  }
  return conversation.title.toLocaleLowerCase().includes(query) ? { conversation } : undefined
}

export function ConversationHistory({ conversations, activeId, onOpen, compact = false }: {
  conversations: Conversation[]
  activeId: string
  onOpen: (id: string, messageId?: string) => void
  compact?: boolean
}) {
  const [query, setQuery] = useState('')
  const [serverTasks, setServerTasks] = useState<WorkspaceTaskRow[]>([])
  useEffect(() => {
    const ids = conversations.filter(item => item.messages.length).map(item => item.id).slice(0, 50)
    if (!ids.length) return
    const request = new AbortController()
    void listWorkspaceTasks(ids, request.signal).then(rows => {
      if (!request.signal.aborted && Array.isArray(rows)) setServerTasks(rows)
    }).catch(() => undefined)
    return () => request.abort()
  }, [conversations])
  const matching = useMemo(() => {
    const search = compactText(query).toLocaleLowerCase()
    return conversations.filter(item => item.messages.length).map(item => historyMatch(item, search))
      .filter(match => match !== undefined)
      .sort((a, b) => b.conversation.updatedAt.localeCompare(a.conversation.updatedAt))
  }, [conversations, query])

  return (
    <section className={`workbench-panel workspace-history${compact ? ' workspace-history-compact' : ''}`} aria-label="历史会话">
      <div className="workspace-history-header">
        {compact ? <h2>历史会话</h2> : <><h1>历史会话</h1><p>搜索此浏览器保存的会话标题、消息和结果。</p></>}
        <Input
          prefix={<SearchOutlined aria-hidden="true" />}
          aria-label="搜索会话"
          placeholder="搜索标题、消息或结果…"
          value={query}
          allowClear
          onChange={event => setQuery(event.target.value)}
        />
      </div>
      {matching.length ? (
        <ul className="workspace-history-list">
          {matching.map(({ conversation, messageId, snippet }) => {
            const { id, title, updatedAt } = conversation
            const latestRun = conversation.activeRun?.runId ?? conversation.messages.at(-1)?.runId
            const task = serverTasks.find(row => row.conversation_id === id && row.current_run_id === latestRun)
            const status = task ? task.partial && task.status === 'COMPLETED' ? 'PARTIAL' : task.status
              : conversationStatus(conversation)
            return (
              <li key={id}>
                <button type="button" aria-current={id === activeId ? 'true' : undefined} onClick={() => onOpen(id, messageId)}>
                  <span className="workspace-row-icon"><MessageOutlined aria-hidden="true" /></span>
                  <span className="workspace-history-copy">
                    <strong title={title}>{title}</strong>
                    {snippet && <span className="workspace-history-snippet">{snippet}</span>}
                    <time dateTime={updatedAt}>{formatActivityTime(updatedAt)}</time>
                  </span>
                  <TaskStatus status={status} />
                </button>
              </li>
            )
          })}
        </ul>
      ) : <p className="workbench-empty">{query.trim() ? '没有匹配的会话。' : '开始对话后，会话会保存在这里。'}</p>}
    </section>
  )
}

const filters = ['全部', '进行中', '等待补充', '需要恢复', '已结束'] as const
type TaskFilter = typeof filters[number]
const inProgress = new Set(['PENDING', 'RUNNING', 'RECOVERING'])
const ended = new Set(['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'RECOVERY_BLOCKED'])

function matchesTaskFilter({ status, can_resume }: WorkspaceTaskRow, filter: TaskFilter) {
  switch (filter) {
    case '进行中': return inProgress.has(status)
    case '等待补充': return status === 'WAITING_FOR_INPUT'
    case '需要恢复': return can_resume
    case '已结束': return ended.has(status) && !can_resume
    default: return true
  }
}

function TaskOverview({ rows }: { rows: WorkspaceTaskRow[] }) {
  const count = (filter: TaskFilter) => rows.filter(row => matchesTaskFilter(row, filter)).length
  const today = new Date()
  const week = Array.from({ length: 7 }, (_, index) => {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (6 - index))
    return { key: day.toDateString(), day: dayFormat.format(day), count: 0 }
  })
  for (const row of rows) {
    const day = week.find(item => item.key === new Date(row.created_at).toDateString())
    if (day) day.count++
  }

  return (
    <aside className="workbench-panel workbench-overview" aria-label="运行概览">
      <h2><HistoryOutlined aria-hidden="true" />运行概览</h2>
      <div className="workbench-overview-metrics">
        {([
          ['进行中', SyncOutlined, 'running'], ['等待补充', ClockCircleOutlined, 'waiting'],
          ['需要恢复', ExclamationCircleOutlined, 'waiting'], ['已结束', CheckCircleOutlined, 'success'],
        ] as const).map(([label, Icon, tone]) => (
          <div className={`workspace-state-${tone}`} key={label}>
            <Icon aria-hidden="true" /><span>{label}<strong>{count(label)}</strong></span>
          </div>
        ))}
      </div>
      <h3>近 7 天发起的任务</h3>
      {week.some(day => day.count > 0) ? (
        <Suspense fallback={<p className="workbench-empty" role="status">正在绘制任务趋势…</p>}>
          <WorkspaceTrend days={week} />
        </Suspense>
      ) : <p className="workbench-empty">这 7 天暂无已加载的运行记录。</p>}
    </aside>
  )
}

export function WorkspaceActivity({ conversations, onOpen, recent = false }: {
  conversations: Conversation[]
  onOpen: (id: string, runId?: string, title?: string) => void
  recent?: boolean
}) {
  const scope = useMemo(() => conversations.filter(item => item.messages.length > 0)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, 50), [conversations])
  const [attempt, setAttempt] = useState(0)
  const [response, setResponse] = useState<{ scope: Conversation[], attempt: number, rows?: WorkspaceTaskRow[] }>()
  const [filter, setFilter] = useState<TaskFilter>('全部')

  useEffect(() => {
    if (!scope.length) return
    const controller = new AbortController()
    listWorkspaceTasks(scope.map(({ id }) => id), controller.signal).then(rows => {
      if (!controller.signal.aborted) setResponse({ scope, attempt, rows })
    }).catch(() => {
      if (!controller.signal.aborted) setResponse({ scope, attempt })
    })
    return () => controller.abort()
  }, [scope, attempt])

  const current = response?.scope === scope && response.attempt === attempt
  const loading = scope.length > 0 && !current
  const unavailable = current && !response.rows
  const scopedRows = useMemo(() => {
    if (!scope.length || !response?.rows) return []
    const ids = new Set(scope.map(({ id }) => id))
    const replies = new Map<string, ChatMessage>()
    for (const { messages } of scope) {
      for (const message of messages) {
        if (message.role === 'assistant' && message.runId) replies.set(message.runId, message)
      }
    }
    return response.rows.filter(row => ids.has(row.conversation_id)).map(row => {
      const reply = row.current_run_id ? replies.get(row.current_run_id) : undefined
      return {
        ...row,
        status: row.status === 'COMPLETED' && (row.partial ?? reply?.partial) ? 'PARTIAL' : row.status,
        specialist: reply?.agentRuns?.[0],
      }
    })
  }, [response, scope])
  const visibleRows = recent ? scopedRows.slice(0, 3) : scopedRows.filter(row => matchesTaskFilter(row, filter))

  const panel = (
    <section className={`workbench-panel workbench-tasks${recent ? ' workbench-recent' : ''}`} aria-label={recent ? '最近任务' : '任务列表'} aria-busy={loading}>
      <div className="workbench-panel-header">
        <h2><ProfileOutlined aria-hidden="true" />{recent ? '最近任务' : '任务列表'}</h2>
        {recent && <a href="#/tasks">查看全部任务 <RightOutlined aria-hidden="true" /></a>}
      </div>
      {!recent && <Segmented aria-label="任务状态" value={filter} onChange={setFilter} options={[...filters]} />}
      {visibleRows.length ? (
        <ul className="workbench-task-list">
          {visibleRows.map(row => {
            const { icon: Icon, name } = specialistPresentation(row.specialist?.agent, row.specialist?.profile)
            return (
              <li key={row.id}>
                <button type="button" onClick={() => onOpen(row.conversation_id, row.current_run_id ?? undefined, row.title)}>
                  <span className="workspace-row-icon" title={row.specialist ? name : undefined}><Icon aria-hidden="true" /></span>
                  <strong title={row.title}>{row.title}</strong>
                  <time dateTime={row.updated_at}>{formatActivityTime(row.updated_at)}</time>
                  <TaskStatus status={row.status} showIcon={recent} />
                  <RightOutlined aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>
      ) : <p className="workbench-empty" role="status">{loading ? '正在读取任务…' : unavailable
        ? '暂时无法读取任务记录。' : scope.length ? '当前没有匹配的任务。' : '开始一轮对话后，任务会出现在这里。'}</p>}
      {unavailable && <Button size="small" onClick={() => setAttempt(count => count + 1)}>重试</Button>}
    </section>
  )

  if (recent) return panel
  return (
    <div className="workspace-activity-page">
      <header className="workspace-page-heading">
        <div><h1>任务工作台</h1><p>查看此浏览器会话的最近 50 条运行记录，打开会话可处理待补充或需要恢复的任务。</p></div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setAttempt(count => count + 1)}>刷新</Button>
      </header>
      <div className="workspace-task-layout">
        {panel}
        {!unavailable && !loading && <TaskOverview rows={scopedRows} />}
      </div>
    </div>
  )
}
