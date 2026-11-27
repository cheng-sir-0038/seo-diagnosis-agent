import {
  ApartmentOutlined, ArrowLeftOutlined, CalendarOutlined, ClockCircleOutlined, ExclamationCircleOutlined, FileTextOutlined,
  PullRequestOutlined, ReloadOutlined, RightOutlined, SearchOutlined, ToolOutlined, WarningOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Empty, Grid, Input, Pagination, Select, Switch, Table, Tabs } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'

import { PageHeader } from '../components/PageHeader'
import { RawJsonResult } from '../components/renderers/RawJsonResult'
import { toolIcon, toolLabel } from '../components/toolLabels'
import { productApi, ProductRequestError } from '../services/productApi'
import { isSpecialist, readSpecialistProfile, specialistPresentation, type SpecialistProfile } from '../specialists'
import type { Conversation } from '../types/agent'
import type { AgentIssue, MetricSummary, ModelCallRecord, ObservabilityEvent, ObservabilityOverview, ObservabilitySpan, ObservabilityTrace, ToolCallRecord } from '../types/product'
import './observability.css'

const dateTime = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
const counter = new Intl.NumberFormat('zh-CN')
const metricNumber = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const metricLabels: Record<string, string> = {
  'trace.duration_ms': 'Trace 总耗时',
  'run.duration_ms': '运行耗时',
  'span.duration_ms': '执行步骤耗时',
  'model.latency_ms': '模型调用耗时',
  'model.tokens.total': '模型 Token 用量',
  'tool.latency_ms': '工具调用耗时',
  'tool.call.count': '工具调用计数',
  'issue.occurrence.count': '问题发生次数',
}
const operationLabels: Record<string, string> = {
  'workspace.documents': '资料问答',
  'workspace.chat': '主 Agent 对话',
  'seo_agent.chat': 'SEO / GEO 站点对话',
  'scheduler.opportunity_scan': '定时扫描 SEO 机会',
  'scheduler.daily_digest': '生成 SEO 日报',
  'scheduler.weekly_digest': '生成 SEO 周报',
}
const timeOptions = [{ value: 24, label: '最近 24 小时' }, { value: 168, label: '最近 7 天' }, { value: 0, label: '全部时间' }]
const severityLabels: Record<string, string> = {
  CRITICAL: '严重', ERROR: '错误', WARNING: '警告', INFO: '信息', DEBUG: '调试',
}
const traceOptions = ['全部状态', '进行中', '已完成', '部分完成', '失败', '等待补充', '已停止', '已中断']
const executionStates: Record<string, { text: string, tone: string }> = {
  RUNNING: { text: '进行中', tone: 'running' },
  COMPLETED: { text: '已完成', tone: 'success' },
  FAILED: { text: '失败', tone: 'error' },
  CANCELLED: { text: '已停止', tone: 'neutral' },
  INTERRUPTED: { text: '已中断', tone: 'neutral' },
}
const promptRoleLabels: Record<string, string> = {
  system: '系统提示词',
  developer: '开发者提示词',
  user: '用户输入',
  human: '用户输入',
}
const stepLabels: Record<string, string> = {
  'workspace-supervisor': '主 Agent',
  'seo-specialist-agent': 'SEO / GEO 专员',
  'workspace.supervisor': '理解需求并分派专员',
  'workspace.skill': '加载业务技能',
  'workspace.procurement': '购物与采购专员',
  'workspace.procurement.intake': '提取商品与报价条件',
  'workspace.procurement.intake.v2': '提取商品与报价条件',
  'workspace.travel.intake': '提取出差条件',
  'seo.page.suggestions': '生成页面 SEO 建议',
  page_seo_suggestions: '生成页面 SEO 建议',
  'seo_page_suggestions.reused': '复用页面建议结果',
  validate_page_suggestions: '核对页面建议的引用',
  restore_page_suggestion_facts: '恢复已取得的页面证据',
  'seo.geo.question_researcher': '研究生成式搜索提问',
  'seo.geo.content_editor': '拟定 GEO 内容改稿',
  'seo.share_image.visual': '规划分享图画面',
  share_image_visual_brief: '规划分享图画面',
  compose_share_image: '生成分享图片',
  generate_share_image: '生成分享图片',
  'seo.opportunity.discover': '识别关键词机会',
  opportunity_discovery: '识别关键词机会',
  'seo.opportunity_discovery': '发现关键词机会',
  'seo.opportunity_discovery.reused': '复用关键词机会',
  freeze_opportunity_evidence: '保存机会依据',
  validate_opportunity_candidates: '核对候选机会',
  persist_opportunity_candidates: '保存候选机会',
  'seo.prd.generate': '撰写任务需求文档',
  generate_prd: '撰写任务需求文档',
  'seo.delivery.review': '审核交付结果',
  review_delivery: '审核交付结果',
  'seo.task_planning': '规划 SEO 任务',
  'seo.task_planning.reused': '复用 SEO 任务计划',
  validate_approved_opportunities: '核对已批准的机会',
  persist_task_and_prd: '保存任务与需求文档',
  load_page_review_baseline: '读取页面变更基线',
  freeze_page_review_evidence: '保存复检依据',
  compare_page_acceptance: '逐项核对页面变更',
  wait_for_user_input: '等待补充信息',
  human_input_received: '接收补充信息',
  api: '接口发起的运行',
}
const eventLabels: Record<string, string> = {
  'trace.started': '创建运行记录',
  'trace.completed': '记录已完成',
  'trace.failed': '记录执行失败',
  'trace.cancelled': '记录已停止',
  'trace.interrupted': '本次执行已暂停',
  'run.started': '开始执行任务',
  'run.resumed': '恢复任务执行',
  'run.completed': '任务执行完成',
  'run.failed': '任务执行失败',
  'run.cancelled': '任务已停止',
  'run.interrupted': '任务暂停',
  'run.waiting_for_input': '等待用户补充',
  'stage.changed': '切换执行阶段',
  'agent.dispatched': '专员开始处理',
  'agent.started': '专员开始处理',
  'agent.completed': '专员处理完成',
  'agent.failed': '专员处理失败',
  'skill.loaded': '加载技能',
  'span.started': '开始执行步骤',
  'span.completed': '步骤执行完成',
  'span.failed': '步骤执行失败',
  'span.cancelled': '步骤已停止',
  'span.interrupted': '步骤暂停',
  'tool.started': '开始调用工具',
  'tool.completed': '工具调用完成',
  'tool.failed': '工具调用失败',
  'input.submitted': '提交补充信息',
  'input.accepted': '接收补充信息',
  'issue.captured': '记录执行问题',
  'message.started': '开始生成回答',
  'message.delta': '生成回答',
  'message.completed': '回答生成完成',
}
type ConversationLink = Pick<Conversation, 'id' | 'title'>
type TraceResponse = { id: string, trace?: ObservabilityTrace, unavailable?: boolean }
type ObservationView = 'traces' | 'issues' | 'metrics'
const observationPath = '#/observability'
// Keep known maintenance records out of list/detail responses from older servers.
const maintenanceOperations = new Set(['scheduler.notification_outbox', 'scheduler.automation_sync'])
const isProductTrace = (trace: ObservabilityTrace) => !maintenanceOperations.has(trace.operation_name)

function subscribeToHash(listener: () => void) {
  window.addEventListener('hashchange', listener)
  return () => window.removeEventListener('hashchange', listener)
}

function readObservationRoute(hash: string) {
  const [path, search] = hash.split('?', 2)
  const params = new URLSearchParams(path === observationPath ? search : undefined)
  const view = params.get('view')
  const tab: ObservationView = view === 'issues' || view === 'metrics' ? view : 'traces'
  const traceId = params.get('trace')
  return { tab, traceId: tab === 'traces' && traceId && traceId.length <= 200 ? traceId : undefined }
}

function navigateObservation(hash: string, mode: 'push' | 'replace', openedFromList = false) {
  const oldURL = window.location.href
  if (window.location.hash === hash) return
  const state = { ...window.history.state, observationDetailFromList: openedFromList }
  if (mode === 'push') window.history.pushState(state, '', hash)
  else window.history.replaceState(state, '', hash)
  // Notify the workbench router as well as the local view after pushState/replaceState.
  window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL, newURL: window.location.href }))
}

function formatTime(value?: string | null) {
  return value && !Number.isNaN(Date.parse(value)) ? dateTime.format(new Date(value)) : '—'
}

function duration(value?: number | null) {
  if (value == null) return '—'
  return value >= 1_000 ? `${(value / 1_000).toFixed(2)} s` : `${Math.round(value)} ms`
}

function metricValue(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : <span title={String(value)}>{metricNumber.format(value)}</span>
}

function readJson(value?: string): unknown {
  if (!value) return undefined
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function readJsonFields(value?: string): Record<string, unknown> {
  const data = readJson(value)
  return data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : {}
}

function traceMetadata(trace: ObservabilityTrace) {
  const payload = readJson(trace.metadata_json)
  if (!payload || typeof payload !== 'object') return {}
  return payload as { application_run_id?: string, application_status?: string, partial?: boolean }
}

function traceState(trace: ObservabilityTrace) {
  const { application_status: applicationStatus, partial } = traceMetadata(trace)
  if (applicationStatus === 'waiting_for_input') return { text: '等待补充', tone: 'waiting' }
  if (partial && trace.status === 'COMPLETED') return { text: '部分完成', tone: 'waiting' }
  return executionStates[trace.status] ?? { text: trace.status, tone: 'neutral' }
}

function TraceState({ trace }: { trace: ObservabilityTrace }) {
  const { text, tone } = traceState(trace)
  return <span className={`workspace-state workspace-state-${tone}`}>{text}</span>
}

function operationLabel(name: string) {
  return Object.hasOwn(operationLabels, name) ? operationLabels[name] : name
}

function describeIssue(issue: AgentIssue) {
  const component = issue.component ?? ''
  const knownTool = component === 'page_seo_suggestions' ? '生成页面 SEO 建议' : toolLabel(component)
  const operation = operationLabel(component)
  const step = Object.hasOwn(stepLabels, component) ? stepLabels[component] : undefined
  const title = knownTool !== component ? `${knownTool}工具失败`
    : step ? `${step}失败`
      : operation !== component ? `${operation}失败`
        : '运行出现问题'
  const recorded = issue.title
  let reason = '展开详情查看记录的错误信息。'
  if (/Failed to parse|JSONDecodeError|OutputParserException/i.test(`${issue.issue_type} ${recorded}`)) {
    reason = '返回内容不符合预期格式，无法读取结果。'
  } else if (/Cannot connect|ConnectionError|ClientConnectorError/i.test(`${issue.issue_type} ${recorded}`)) {
    reason = '无法连接目标页面或数据服务。'
  } else if (/Timeout|timed out|超时/i.test(`${issue.issue_type} ${recorded}`)) {
    reason = '等待响应超时。'
  } else if (/页面 HTML 请求失败/i.test(recorded)) {
    reason = '未能获取目标页面的内容。'
  }
  return { title, reason }
}

function IssueSeverity({ severity }: { severity: string }) {
  return <span className="obs-issue-severity" data-severity={severity}>{severityLabels[severity] ?? severity}</span>
}

export function ObservabilityPage({ conversationForRun, onOpenConversation }: {
  conversationForRun?: (runId: string) => ConversationLink | undefined
  onOpenConversation?: (conversationId: string, runId?: string) => void
} = {}) {
  const { xl: wide } = Grid.useBreakpoint()
  const [attempt, setAttempt] = useState(0)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [hours, setHours] = useState(24)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('全部状态')
  const hash = useSyncExternalStore(subscribeToHash, () => window.location.hash)
  const { tab, traceId: activeTraceId } = readObservationRoute(hash)
  const tableRef = useRef<HTMLElement>(null)
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const lastRoute = useRef<{ tab: ObservationView, traceId?: string }>(undefined)
  const [tracePage, setTracePage] = useState(1)
  const [detailTab, setDetailTab] = useState<{ traceId: string, tab: string }>()
  const [detailResponse, setDetailResponse] = useState<TraceResponse>()
  const [detailAttempt, setDetailAttempt] = useState(0)
  const [issuePage, setIssuePage] = useState(1)
  const [lookupTarget, setLookupTarget] = useState<{ identifier: string }>()
  const [lookupResponse, setLookupResponse] = useState<{ target: typeof lookupTarget, traces?: ObservabilityTrace[] }>()
  const [response, setData] = useState<{
    overview?: ObservabilityOverview
    traces?: ObservabilityTrace[]
    issues?: AgentIssue[]
    metrics?: MetricSummary[]
    attempt: number
    hours: number
    incomplete: boolean
  }>()
  const data = response?.hours === hours ? response : undefined
  const loading = data?.attempt !== attempt
  const scopedTraces = data?.traces?.filter(isProductTrace)
  const traceLoading = loading
  const visibleIssues = data?.issues?.filter(issue => !maintenanceOperations.has(issue.component ?? ''))
  const issuePageSize = 10
  const currentIssuePage = Math.min(issuePage, Math.max(1, Math.ceil((visibleIssues?.length ?? 0) / issuePageSize)))

  useEffect(() => {
    const controller = new AbortController()
    const signal = controller.signal
    const now = Date.now()
    const timeWindow = hours ? {
      start_at: new Date(now - hours * 3_600_000).toISOString(),
      end_at: new Date(now).toISOString(),
    } : undefined
    void Promise.allSettled([
      productApi.getObservabilityOverview(timeWindow, signal, true),
      productApi.listObservabilityTraces(undefined, signal, timeWindow, 'product'),
      productApi.listAgentIssues(undefined, signal, timeWindow, undefined, true),
      productApi.listMetricSummary(undefined, signal, timeWindow, true),
    ]).then(([overview, traces, issues, metrics]) => {
      if (signal.aborted) return
      setData({
        overview: overview.status === 'fulfilled' ? overview.value : undefined,
        traces: traces.status === 'fulfilled' ? traces.value : undefined,
        issues: issues.status === 'fulfilled' ? issues.value : undefined,
        metrics: metrics.status === 'fulfilled' ? metrics.value : undefined,
        attempt,
        hours,
        incomplete: [overview, traces, issues, metrics].some(result => result.status === 'rejected'),
      })
    })
    return () => controller.abort()
  }, [attempt, hours])

  useEffect(() => {
    if (!autoRefresh || traceLoading) return
    const timer = window.setTimeout(() => setAttempt(count => count + 1), 30_000)
    return () => window.clearTimeout(timer)
  }, [autoRefresh, traceLoading, attempt])

  useEffect(() => {
    if (!lookupTarget) return
    const controller = new AbortController()
    productApi.findObservabilityTraces(lookupTarget.identifier, controller.signal, 'product').then(traces => {
      if (!controller.signal.aborted) setLookupResponse({ target: lookupTarget, traces: traces.filter(isProductTrace) })
    }).catch(() => {
      if (!controller.signal.aborted) setLookupResponse({ target: lookupTarget })
    })
    return () => controller.abort()
  }, [lookupTarget])

  const lookup = lookupTarget && lookupResponse?.target === lookupTarget ? lookupResponse : undefined
  const normalizedQuery = query.trim().toLowerCase()
  const linkedConversation = (trace: ObservabilityTrace) => {
    const { application_run_id: runId } = traceMetadata(trace)
    return runId ? conversationForRun?.(runId) : undefined
  }
  const labelForTrace = (trace: ObservabilityTrace) => linkedConversation(trace)?.title ?? operationLabel(trace.operation_name)
  const visibleTraces = (scopedTraces ?? []).filter(trace => {
    const { application_run_id: runId } = traceMetadata(trace)
    const matchesStatus = status === '全部状态' || traceState(trace).text === status
    return matchesStatus && [trace.id, trace.operation_name, labelForTrace(trace), runId]
      .some(value => value?.toLowerCase().includes(normalizedQuery))
  })
  const traceRows = lookupTarget ? lookup?.traces ?? [] : visibleTraces
  const tracePageSize = 7
  const currentTracePage = Math.min(tracePage, Math.max(1, Math.ceil(traceRows.length / tracePageSize)))
  const selectedResponse = detailResponse?.id === activeTraceId ? detailResponse : undefined
  const activeTrace = selectedResponse?.trace ?? traceRows.find(trace => trace.id === activeTraceId)

  function selectTrace(id: string) {
    const params = new URLSearchParams({ trace: id })
    navigateObservation(`${observationPath}?${params}`, activeTraceId ? 'replace' : 'push',
      !activeTraceId || Boolean(window.history.state?.observationDetailFromList))
  }

  function closeTrace() {
    if (window.history.state?.observationDetailFromList) window.history.back()
    else navigateObservation(observationPath, 'replace')
  }

  function changeTab(nextTab: string) {
    if (nextTab === tab) return
    navigateObservation(nextTab === 'traces' ? observationPath : `${observationPath}?view=${nextTab}`, 'push')
  }

  useEffect(() => {
    const previous = lastRoute.current
    lastRoute.current = { tab, traceId: activeTraceId }
    if (activeTraceId && activeTraceId !== previous?.traceId) {
      detailHeading.current?.focus()
    } else if (previous?.tab !== undefined && previous.tab !== tab) {
      tableRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus()
    } else if (previous?.traceId && !activeTraceId) {
      const opener = Array.from(tableRef.current?.querySelectorAll<HTMLElement>('[data-trace-trigger]') ?? [])
        .find(button => button.dataset.traceTrigger === previous.traceId)
      const focusTarget = opener ?? tableRef.current?.querySelector<HTMLElement>('input')
      focusTarget?.focus()
    }
  }, [tab, activeTraceId])

  useEffect(() => {
    if (!activeTraceId) return
    const controller = new AbortController()
    productApi.getObservabilityTrace(activeTraceId, controller.signal, true).then(trace => {
      if (!controller.signal.aborted) setDetailResponse(isProductTrace(trace)
        ? { id: activeTraceId, trace }
        : { id: activeTraceId, unavailable: true })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setDetailResponse({
        id: activeTraceId, unavailable: error instanceof ProductRequestError && error.status === 404,
      })
    })
    return () => controller.abort()
  }, [activeTraceId, attempt, detailAttempt])
  const overview = data?.overview
  const previous = overview?.previous
  const comparison = hours === 24 ? '前 24 小时' : '前 7 天'
  const comparableTraces = previous && previous.trace_count > 0 ? previous : undefined
  const comparableTools = previous && previous.tool_call_count > 0 ? previous.tool_call_count : undefined

  function trend(value?: number, baseline?: number) {
    if (value === undefined || baseline === undefined || !hours) return undefined
    const delta = value - baseline
    return delta === 0 ? `与${comparison}持平` : `较${comparison} ${delta > 0 ? '+' : '−'}${counter.format(Math.abs(delta))}`
  }

  const traceColumns: ColumnsType<ObservabilityTrace> = [
    {
      title: '运行', dataIndex: 'operation_name',
      render: (_, trace) => {
        const label = labelForTrace(trace)
        const operation = operationLabel(trace.operation_name)
        const secondary = lookupTarget?.identifier
          ? lookupTarget.identifier
          : label !== operation ? operation : undefined
        return (
          <button type="button" className="obs-trace-button" data-trace-trigger={trace.id} onClick={() => selectTrace(trace.id)} aria-expanded={activeTraceId === trace.id} aria-controls={activeTraceId === trace.id ? 'obs-trace-detail' : undefined}>
            <PullRequestOutlined aria-hidden="true" />
            <span>
              <strong title={label}>{label}</strong>
              {secondary && <small className={lookupTarget ? 'obs-trace-match' : undefined} title={secondary}>{secondary}</small>}
            </span>
          </button>
        )
      },
    },
    { title: '状态', width: 84, render: (_, trace) => <TraceState trace={trace} /> },
    { title: '耗时', dataIndex: 'duration_ms', width: 75, align: 'right', render: duration },
    { title: '开始时间', dataIndex: 'started_at', width: 120, render: value => <time className="obs-time" dateTime={value}>{formatTime(value)}</time> },
  ]

  const metricColumns: ColumnsType<MetricSummary> = [
    { title: '指标', dataIndex: 'metric_name', width: '32%', render: (name: string) => {
      const label = Object.hasOwn(metricLabels, name) ? metricLabels[name] : undefined
      return <span className="obs-metric-name"><strong>{label ?? name}</strong>{label && <code>{name}</code>}</span>
    } },
    { title: '样本数', dataIndex: 'point_count', width: '13%', align: 'right', render: metricValue },
    { title: '平均值', dataIndex: 'average_value', width: '15%', align: 'right', render: metricValue },
    { title: '最小值', dataIndex: 'minimum_value', width: '15%', align: 'right', render: metricValue },
    { title: '最大值', dataIndex: 'maximum_value', width: '15%', align: 'right', render: metricValue },
    { title: '单位', dataIndex: 'unit', width: '10%', render: value => <span className="obs-metric-unit">{value === 'calls' || value === 'occurrences' ? '次' : value ?? '—'}</span> },
  ]

  const detail = activeTraceId && (
    <TraceDetail
      key={activeTraceId}
      response={selectedResponse}
      activeTab={detailTab?.traceId === activeTraceId ? detailTab.tab : 'tree'}
      onTabChange={tab => setDetailTab({ traceId: activeTraceId, tab })}
      title={activeTrace ? labelForTrace(activeTrace) : ''}
      conversation={activeTrace ? linkedConversation(activeTrace) : undefined}
      onOpenConversation={onOpenConversation}
      onRetry={() => setDetailAttempt(count => count + 1)}
    />
  )

  return (
    <div className="obs-console">
      <PageHeader
        title="运行中心 / 观测面板"
        description="追踪业务运行，查看工具调用和已记录的问题。"
        actions={(
          <>
            <Select
              aria-label="观测时间范围"
              prefix={<CalendarOutlined aria-hidden="true" />}
              value={hours}
              onChange={value => {
                setHours(value)
                setAttempt(count => count + 1)
                setTracePage(1)
                setLookupTarget(undefined)
                setIssuePage(1)
              }}
              options={timeOptions}
            />
            <Button aria-label="刷新观测" icon={<ReloadOutlined />} loading={loading} onClick={() => setAttempt(value => value + 1)}>刷新</Button>
            <label className="obs-refresh-switch">自动刷新（30 秒）<Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} aria-label="自动刷新" /></label>
          </>
        )}
      />
      {data?.incomplete && <Alert type="warning" showIcon title="部分观测数据暂时无法读取，刷新后可重试。" />}
      <section className="obs-metrics" aria-label="运行指标">
        {([
          ['Trace 记录', overview?.trace_count, trend(overview?.trace_count, comparableTraces?.trace_count), '记录业务执行及计划任务结果', ApartmentOutlined, 'blue'],
          ['整次运行失败', overview?.failed_trace_count, trend(overview?.failed_trace_count, comparableTraces?.failed_trace_count), '仅统计失败的 Trace，工具报错见「问题」', ExclamationCircleOutlined, 'red'],
          ['工具调用记录', overview?.tool_call_count, trend(overview?.tool_call_count, comparableTools), '按调用开始时间', ToolOutlined, 'violet'],
          ['等待补充 Trace', overview?.waiting_trace_count, undefined, '按 Trace 当前状态', ClockCircleOutlined, 'amber'],
        ] as const).map(([label, value, change, description, Icon, tone]) => (
          <article className={`obs-metric obs-metric--${tone === 'red' && value === 0 ? 'neutral' : tone}`} key={label} aria-label={label}>
            <div className="obs-metric-heading"><span>{label}</span><Icon className="obs-metric-icon" aria-hidden="true" /></div>
            <strong>{value == null ? '—' : counter.format(value)}</strong>
            {tone === 'red' || tone === 'blue' ? (
              <div className="obs-metric-caption">
                <small>{description}</small>
                {change && (tone !== 'red' || value !== comparableTraces?.failed_trace_count) && <small>{change}</small>}
              </div>
            ) : <small title={description}>{change ?? description}</small>}
          </article>
        ))}
      </section>
      <div className={`obs-layout${wide && activeTraceId ? ' with-trace' : ''}`}>
        <section ref={tableRef} className="obs-panel obs-table" aria-label="观测记录">
          <Tabs activeKey={tab} onChange={changeTab} animated={false} items={[
            {
              key: 'traces', label: 'Trace', children: (
                <>
                  <div className="obs-filter">
                    <Input prefix={<SearchOutlined aria-hidden="true" />} aria-label="搜索 Trace" placeholder="搜索标题、操作名称或 ID…" value={query} onChange={event => { setQuery(event.target.value); setLookupTarget(undefined); setTracePage(1) }} allowClear />
                    <Select aria-label="Trace 状态" disabled={Boolean(lookupTarget)} value={status} onChange={value => { setStatus(value); setTracePage(1) }} options={traceOptions.map(value => ({ value, label: value }))} />
                    <Button disabled={!query.trim() || query.trim().length > 200} loading={Boolean(lookupTarget && !lookup)} onClick={() => {
                      setLookupTarget({ identifier: query.trim() })
                      setTracePage(1)
                    }}>按 ID 查找</Button>
                    {!lookupTarget && scopedTraces && <span className="obs-filter-count">{counter.format(visibleTraces.length)} 条已加载记录</span>}
                  </div>
                  {lookupTarget && <div className="obs-list-note obs-lookup-note">
                    <span>跨时间精确匹配 Trace ID 或运行 ID。</span>
                    <Button size="small" type="link" onClick={() => { setLookupTarget(undefined); setQuery(''); setTracePage(1) }}>返回列表</Button>
                  </div>}
                  {!lookupTarget && data?.traces?.length === 100 && (
                    <p className="obs-list-note">本次最多加载 100 条 Trace。查找更早的记录可使用「按 ID 查找」。</p>
                  )}
                  <Table
                    key={lookupTarget ? 'lookup' : 'traces'}
                    className="obs-traces-grid"
                    rowKey="id"
                    rowClassName={trace => trace.id === activeTraceId ? 'obs-trace-selected' : ''}
                    columns={traceColumns}
                    dataSource={traceRows}
                    loading={lookupTarget ? !lookup : traceLoading}
                    size="small"
                    scroll={{ x: 410 }}
                    pagination={{ current: currentTracePage, onChange: setTracePage, pageSize: tracePageSize, hideOnSinglePage: true, showSizeChanger: false }}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={lookupTarget ? (lookup && !lookup.traces ? '无法按 ID 查找，请重试' : '没有匹配此 ID 的 Trace') : !scopedTraces && !traceLoading ? '暂时无法读取 Trace' : normalizedQuery || status !== '全部状态' ? '没有匹配的 Trace' : '暂无运行记录'} /> }}
                  />
                </>
              ),
            },
            {
              key: 'issues', label: '问题', children: (
                <>
                  <p className="obs-list-note">{visibleIssues?.length === 100 ? '显示最新 100 条问题，' : ''}按业务运行中最近出现的时间排序，次数为累计值。</p>
                  {visibleIssues?.length ? (
                    <>
                      <ul className="obs-issue-list" aria-label="已记录的问题" aria-busy={loading}>
                        {visibleIssues.slice((currentIssuePage - 1) * issuePageSize, currentIssuePage * issuePageSize).map(issue => {
                          const { title, reason } = describeIssue(issue)
                          return <li className="obs-issue" key={issue.id}>
                            <div className="obs-issue-main">
                              <WarningOutlined className="obs-issue-icon" aria-hidden="true" />
                              <div className="obs-issue-copy">
                                <div className="obs-issue-heading">
                                  <h3>{title}</h3>
                                  <IssueSeverity severity={issue.severity} />
                                </div>
                                <p>{reason}</p>
                              </div>
                            </div>
                            <dl className="obs-issue-meta">
                              <div>
                                <dt>最近出现</dt>
                                <dd><time dateTime={issue.last_seen_at}>{formatTime(issue.last_seen_at)}</time></dd>
                              </div>
                              <div>
                                <dt>累计次数</dt>
                                <dd><strong>{metricValue(issue.occurrence_count)} 次</strong></dd>
                              </div>
                            </dl>
                            <details className="obs-issue-detail">
                              <summary className="workbench-disclosure">
                                <span>技术详情</span>
                              </summary>
                              <div className="obs-issue-detail-body">
                                <dl>
                                  <div><dt>问题 ID</dt><dd>{issue.id}</dd></div>
                                  {issue.component && <div><dt>组件</dt><dd>{issue.component}</dd></div>}
                                  <div><dt>错误类型</dt><dd>{issue.issue_type}</dd></div>
                                </dl>
                                <div className="obs-issue-error-label">已记录的报错摘要</div>
                                <pre>{issue.title}</pre>
                              </div>
                            </details>
                          </li>
                        })}
                      </ul>
                      {visibleIssues.length > issuePageSize && <Pagination className="obs-issue-pagination" size="small" current={currentIssuePage} pageSize={issuePageSize} total={visibleIssues.length} showSizeChanger={false} onChange={setIssuePage} />}
                    </>
                  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在读取问题…' : visibleIssues ? '当前时间范围没有记录问题' : '暂时无法读取问题'} />}
                </>
              ),
            },
            {
              key: 'metrics', label: '指标', children: (
                <Table className="obs-metrics-grid" rowKey={row => `${row.metric_name}:${row.unit ?? ''}`}
                  columns={metricColumns} dataSource={data?.metrics} loading={loading} size="small"
                  tableLayout="fixed" scroll={{ x: 570 }} pagination={false} locale={{ emptyText: !data?.metrics && !loading ? '暂时无法读取指标' : '暂无指标' }} />
              ),
            },
          ]} />
          {tab === 'traces' && !lookupTarget && Boolean(visibleIssues?.length) && (
            <section className="obs-issues-preview" aria-label="最近问题">
              <div className="obs-panel-heading">
                <h2><WarningOutlined aria-hidden="true" />最近问题</h2>
                <Button type="link" onClick={() => { setIssuePage(1); changeTab('issues') }}>查看问题列表</Button>
              </div>
              <ul>{visibleIssues?.slice(0, 4).map(issue => <li key={issue.id}><strong>{describeIssue(issue).title}</strong><IssueSeverity severity={issue.severity} /><time dateTime={issue.last_seen_at}>{formatTime(issue.last_seen_at)}</time></li>)}</ul>
            </section>
          )}
        </section>
        {wide && activeTraceId && (
          <aside id="obs-trace-detail" className="obs-panel obs-detail" aria-label="Trace 详情" onKeyDown={event => {
            if (event.key === 'Escape' && !event.defaultPrevented) closeTrace()
          }}>
            <div className="obs-panel-heading">
              <h2 ref={detailHeading} tabIndex={-1}><PullRequestOutlined aria-hidden="true" />Trace 详情</h2>
              <Button type="text" size="small" icon={<ArrowLeftOutlined aria-hidden="true" />} onClick={closeTrace}>返回列表</Button>
            </div>
            {detail}
          </aside>
        )}
      </div>
      {!wide && (
        <Drawer title="Trace 详情" className="obs-drawer" open={Boolean(activeTraceId)} size="large"
          closable={false} onClose={closeTrace}
          extra={<Button type="text" size="small" icon={<ArrowLeftOutlined aria-hidden="true" />} onClick={closeTrace}>返回列表</Button>}>
          <div id="obs-trace-detail">{detail}</div>
        </Drawer>
      )}
    </div>
  )
}

function specialistOperationId(name: string) {
  const id = /^(?:workspace\.|specialist:)(.+)$/.exec(name)?.[1]
  return isSpecialist(id) ? id : undefined
}

function stepLabel(name: string, documentRun: boolean, fallback = '执行步骤') {
  if (/^workspace\.route(?:\.v\d+(?:\.[0-9a-f]{12})?)?$/.test(name)) return '理解需求并分派专员'
  if (name === 'stream') return documentRun ? '生成资料答复' : '生成答复'
  const iteration = /^model\.iteration\.(\d+)$/.exec(name)
  if (iteration) return documentRun ? '生成资料答复' : `模型处理 · 第 ${iteration[1]} 轮`
  if (Object.hasOwn(stepLabels, name)) return stepLabels[name]
  if (Object.hasOwn(operationLabels, name)) return operationLabels[name]
  const agent = specialistOperationId(name)
  return agent ? `${specialistPresentation(agent).name}专员` : fallback
}

function toolTitle(name: string) {
  const label = toolLabel(name)
  return label === name ? '工具调用' : label
}

function spanPresentation(span: ObservabilitySpan, documentRun: boolean, profiles: ReadonlyMap<string, SpecialistProfile>) {
  const name = span.operation_name
  if (span.kind === 'AGENT') {
    const metadata = readJsonFields(span.metadata_json)
    const agent = isSpecialist(metadata.agent) ? metadata.agent : specialistOperationId(name)
    const profile = readSpecialistProfile(metadata.agent_profile) ?? (agent ? profiles.get(agent) : undefined)
    if (profile || agent) {
      const presentation = specialistPresentation(agent, profile)
      return { title: profile?.name ?? stepLabel(name, documentRun, presentation.name), icon: presentation.icon }
    }
  }
  if (span.kind === 'TOOL') {
    const title = name.endsWith('.reused')
      ? stepLabel(name, documentRun, `复用结果 · ${toolTitle(name.slice(0, -'.reused'.length))}`)
      : toolTitle(name)
    return { title, icon: toolIcon(name) }
  }
  const model = span.kind === 'MODEL'
  return { title: stepLabel(name, documentRun, model ? '模型处理' : '执行步骤'), icon: model ? FileTextOutlined : ApartmentOutlined }
}

function SpanTree({ spans, callsBySpan, documentRun, profiles }: {
  spans: ObservabilitySpan[]
  callsBySpan: ReadonlyMap<string, ToolCallRecord[]>
  documentRun: boolean
  profiles: ReadonlyMap<string, SpecialistProfile>
}) {
  const parents = new Set(spans.map(span => span.id))
  const groups = new Map<string | null, ObservabilitySpan[]>()
  for (const span of spans) {
    const parent = span.parent_span_id && parents.has(span.parent_span_id) ? span.parent_span_id : null
    const current = groups.get(parent) ?? []
    groups.set(parent, [...current, span])
  }
  function tree(parent: string | null) {
    const nodes = groups.get(parent)
    if (!nodes?.length) return null
    return (
      <ul className="obs-tree">
        {nodes.map(span => {
          const calls = callsBySpan.get(span.id)
          const presentation = spanPresentation(span, documentRun, profiles)
          return (
            <li key={span.id}>
              {calls ? calls.map(call => <ToolCallDetails key={call.id} call={call} />) : (
                <CallHeader {...presentation} status={span.status} latency={span.duration_ms} />
              )}
              {tree(span.id)}
            </li>
          )
        })}
      </ul>
    )
  }
  return tree(null)
}

function JsonEvidence({ label, value, identifier, event }: { label: ReactNode, value?: string, identifier?: string, event?: boolean }) {
  const [open, setOpen] = useState(false)
  if (!value && !identifier) return null
  const data = open ? readJson(value) : undefined
  return (
    <details className={event ? 'obs-json obs-event-record' : 'obs-json'} onToggle={toggle => setOpen(toggle.currentTarget.open)}>
      <summary className="workbench-disclosure">{label}</summary>
      {open && <div className="obs-json-body">
        {identifier && <code className="obs-event-id">{identifier}</code>}
        {value && (typeof data === 'string' ? <pre className="json-preview">{data}</pre> : <RawJsonResult data={data} />)}
      </div>}
    </details>
  )
}

function CallHeader({ title, identifier, status, latency, icon: Icon }: {
  title: string
  identifier?: string
  status: string
  latency?: number | null
  icon: typeof ToolOutlined
}) {
  const state = executionStates[status] ?? { text: status, tone: 'neutral' }
  return (
    <span className="obs-call-header">
      <span className="workspace-row-icon"><Icon aria-hidden="true" /></span>
      <span><strong>{title}</strong>{identifier && identifier !== title && <code>{identifier}</code>}</span>
      <span className={`workspace-state workspace-state-${state.tone}`}>{state.text}</span>
      <small>{duration(latency)}</small>
    </span>
  )
}

function ToolCallDetails({ call }: { call: ToolCallRecord }) {
  return (
    <details className="obs-tool-record">
      <summary className="workbench-disclosure">
        <CallHeader title={toolTitle(call.tool_name)}
          status={call.status} latency={call.latency_ms} icon={toolIcon(call.tool_name)} />
      </summary>
      <div className="obs-tool-body">
        <dl className="obs-tool-meta">
          <div><dt>工具标识</dt><dd><code>{call.tool_name}</code></dd></div>
          {call.tool_call_id && <div><dt>调用 ID</dt><dd><code>{call.tool_call_id}</code></dd></div>}
          <div><dt>开始时间</dt><dd>{formatTime(call.started_at)}</dd></div>
          {call.adapter_key && <div><dt>适配器</dt><dd>{call.adapter_key}</dd></div>}
          {call.timeout_seconds != null && <div><dt>超时上限</dt><dd>{call.timeout_seconds} 秒</dd></div>}
          <div><dt>重试次数</dt><dd>{counter.format(call.retry_count ?? 0)}</dd></div>
        </dl>
        {(call.error_message || call.error_code) && (
          <p className="obs-call-error" title={call.error_code ?? undefined}>{call.error_message ?? call.error_code}</p>
        )}
        <JsonEvidence label="调用参数" value={call.arguments_json} />
        <JsonEvidence label="返回摘要" value={call.result_summary_json} />
      </div>
    </details>
  )
}

function TraceToolCalls({ calls }: { calls?: ToolCallRecord[] }) {
  if (!calls?.length) return <p className="workbench-empty">暂无已记录的工具调用。</p>
  return (
    <ol className="obs-call-list" aria-label="已记录的工具调用">
      {calls.map(call => <li key={call.id}><ToolCallDetails call={call} /></li>)}
    </ol>
  )
}

function recordedPrompts(value?: string) {
  const input = readJson(value)
  if (!input || typeof input !== 'object' || !('messages' in input) || !Array.isArray(input.messages)) return []
  return input.messages.flatMap((message: unknown) => {
    if (!message || typeof message !== 'object' || !('content' in message) || !message.content) return []
    const role = 'role' in message ? message.role : 'type' in message ? message.type : undefined
    if (typeof role !== 'string' || !Object.hasOwn(promptRoleLabels, role)) return []
    if ((role === 'user' || role === 'human') && 'business_object' in input) {
      const context = typeof message.content === 'string' ? readJson(message.content) : message.content
      if (context && typeof context === 'object' && !Array.isArray(context)) {
        if ('user_message' in context && typeof context.user_message === 'string' && context.user_message) {
          return [{ label: '用户输入', text: context.user_message }]
        }
        if ('conversation' in context && Array.isArray(context.conversation)) {
          const lastUser = context.conversation.slice().reverse().find(message =>
            message && typeof message === 'object' && message.role === 'user' && typeof message.content === 'string')
          if (lastUser) return [{ label: '用户输入', text: lastUser.content }]
        }
        return []
      }
    }
    const text = typeof message.content === 'string' ? message.content : JSON.stringify(message.content, null, 2)
    return [{ label: promptRoleLabels[role], text }]
  })
}

function PromptEvidence({ label, text }: { label: string, text: string }) {
  // Split only the explicit schema suffix appended by the structured model.
  const separator = /(\r?\n仅输出符合以下 JSON Schema 的 JSON 对象，不要输出解释或 Markdown：)\r?\n(?=\s*\{)/
  const match = label === '系统提示词' ? separator.exec(text) : null
  const schema = match ? text.slice(match.index + match[0].length) : undefined
  const body = match ? text.slice(0, match.index + match[1].length) : text

  return (
    <details className="obs-prompt-section">
      <summary className="workbench-disclosure">{label}</summary>
      <div className="obs-prompt-copy" role="region" aria-label={`${label}正文`} tabIndex={0}>{body}</div>
      {schema && <div className="obs-prompt-schema"><JsonEvidence label="JSON Schema" value={schema} /></div>}
    </details>
  )
}

function recordedModelLabel(call: ModelCallRecord): string | undefined {
  if (!call.model || call.model === 'unknown') return undefined
  const provider = call.provider === 'openai' ? 'OpenAI'
    : call.provider === 'deepseek' ? 'DeepSeek' : call.provider
  return provider ? `${provider} / ${call.model}` : call.model
}

function TracePrompts({ calls, documentRun }: { calls?: ModelCallRecord[], documentRun: boolean }) {
  if (!calls?.length) return <p className="workbench-empty">暂无已记录的模型调用。</p>
  return (
    <ol className="obs-call-list" aria-label="已记录的模型调用">
      {calls.map(call => {
        const prompts = recordedPrompts(call.input_summary_json)
        const key = call.prompt_key ?? call.operation
        const title = stepLabel(key, documentRun, documentRun ? '生成资料答复' : '模型调用')
        return (
          <li key={call.id}>
            <CallHeader title={title} identifier={recordedModelLabel(call)}
              status={call.status} latency={call.latency_ms} icon={FileTextOutlined} />
            {(call.prompt_version || call.total_tokens != null) && (
              <div className="obs-call-meta">
                {call.prompt_version && <span title={call.prompt_version_id ?? undefined}>版本 {call.prompt_version}</span>}
                {call.total_tokens != null && <span>{counter.format(call.total_tokens)} tokens</span>}
              </div>
            )}
            {call.error_code && <p className="obs-call-error">{call.error_code}</p>}
            {prompts.length ? prompts.map(({ label, text }, index) => (
              <PromptEvidence key={`${label}-${index}`} label={label} text={text} />
            )) : <p className="obs-prompt-empty">暂无已记录的提示词正文。</p>}
            <details className="obs-json">
              <summary className="workbench-disclosure">技术信息</summary>
              <dl className="obs-tool-meta">
                {call.prompt_key && <div><dt>提示词标识</dt><dd><code>{call.prompt_key}</code></dd></div>}
                {call.operation !== call.prompt_key && <div><dt>操作标识</dt><dd><code>{call.operation}</code></dd></div>}
              </dl>
            </details>
          </li>
        )
      })}
    </ol>
  )
}

function TraceEvents({ events, spans, documentRun, profiles }: {
  events?: ObservabilityEvent[]
  spans: ObservabilitySpan[]
  documentRun: boolean
  profiles: ReadonlyMap<string, SpecialistProfile>
}) {
  if (!events?.length) return <p className="workbench-empty">暂无事件日志。</p>
  const groups: ObservabilityEvent[][] = []
  for (const event of events) {
    const last = groups.at(-1)
    if (event.event_type === 'message.delta' && last?.[0].event_type === 'message.delta'
      && event.span_id === last[0].span_id) last.push(event)
    else groups.push([event])
  }
  const bySpan = new Map(spans.map(span => [span.id, span]))
  return (
    <ol className="obs-events" aria-label="运行事件">
      {groups.map(group => {
        const event = group[0]
        const span = event.span_id ? bySpan.get(event.span_id) : undefined
        const fields = readJsonFields(event.payload_json)
        const participant = event.event_type.startsWith('agent.') && isSpecialist(fields.agent)
          ? specialistPresentation(fields.agent, readSpecialistProfile(fields.agent_profile) ?? profiles.get(fields.agent))
          : undefined
        const subject = event.event_type.startsWith('tool.') && typeof fields.name === 'string' ? toolTitle(fields.name)
          : participant?.name ?? (event.event_type.startsWith('span.') && span ? spanPresentation(span, documentRun, profiles).title
            : event.event_type === 'skill.loaded' && typeof fields.label === 'string' ? fields.label : undefined)
        const label = event.event_type === 'tool.completed' && fields.reused === true ? '已复用工具结果'
          : Object.hasOwn(eventLabels, event.event_type) ? eventLabels[event.event_type] : '运行事件'
        const description = [label, subject, group.length > 1 ? `${group.length} 段` : undefined].filter(Boolean).join(' · ')
        const details = group.length > 1
          ? JSON.stringify(group.map(({ sequence, payload_json }) => ({ sequence, payload: readJson(payload_json) })))
          : event.payload_json
        const Icon = event.event_type.startsWith('tool.') && typeof fields.name === 'string' ? toolIcon(fields.name)
          : participant?.icon ?? (event.event_type.startsWith('message.') ? FileTextOutlined : ClockCircleOutlined)
        return (
          <li key={event.id} data-severity={event.severity}>
            <JsonEvidence event value={details} identifier={event.event_type} label={<>
              <span className="workspace-row-icon"><Icon aria-hidden="true" /></span>
              <strong className="obs-event-copy">{description}</strong>
              <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
            </>} />
          </li>
        )
      })}
    </ol>
  )
}

function TraceDetail({ response, activeTab, onTabChange, title, conversation, onOpenConversation, onRetry }: {
  response?: TraceResponse
  activeTab: string
  onTabChange: (tab: string) => void
  title: string
  conversation?: ConversationLink
  onOpenConversation?: (conversationId: string, runId?: string) => void
  onRetry: () => void
}) {
  const [identifiersOpen, setIdentifiersOpen] = useState(false)
  const identifiersId = useId()
  if (!response) return <p className="workbench-empty" role="status">正在读取 Trace…</p>
  const { trace } = response
  if (response.unavailable) return <p className="workbench-empty" role="status">这条运行记录不存在或不在运行中心的展示范围内。</p>
  if (!trace) return (
    <div><p className="workbench-empty" role="alert">暂时无法读取 Trace 详情。</p><Button size="small" onClick={onRetry}>重试</Button></div>
  )
  const { application_run_id: runId } = traceMetadata(trace)
  const documentRun = trace.operation_name === 'workspace.documents'
  const runs = trace.runs ?? []
  const spans = runs.flatMap(run => run.spans ?? [])
  const profiles = new Map<string, SpecialistProfile>()
  for (const event of trace.events ?? []) {
    if (!/^(?:agent\.|skill\.loaded$|tool\.)/.test(event.event_type)) continue
    const { agent, agent_profile: value } = readJsonFields(event.payload_json)
    const profile = readSpecialistProfile(value)
    if (isSpecialist(agent) && profile) profiles.set(agent, profile)
  }
  const spanIds = new Set(spans.map(span => span.id))
  const callsBySpan = new Map<string, ToolCallRecord[]>()
  const unlinkedCalls: ToolCallRecord[] = []
  for (const call of trace.tool_calls ?? []) {
    if (call.span_id && spanIds.has(call.span_id)) {
      callsBySpan.set(call.span_id, [...(callsBySpan.get(call.span_id) ?? []), call])
    } else {
      unlinkedCalls.push(call)
    }
  }
  const hasToolSteps = spans.some(span => span.kind === 'TOOL')
  const toolCount = trace.tool_calls?.length ?? 0
  const models = [...new Set(trace.model_calls?.flatMap(call => recordedModelLabel(call) ?? []) ?? [])]

  return (
    <div className="obs-detail-body">
      <div className="obs-trace-heading">
        <strong>{title}</strong>
        <TraceState trace={trace} />
      </div>
      <dl className="obs-trace-summary" aria-label="运行摘要">
        <div><dt>开始时间</dt><dd>{formatTime(trace.started_at)}</dd></div>
        <div><dt>总耗时</dt><dd>{duration(trace.duration_ms)}</dd></div>
        <div><dt>工具明细</dt><dd>{counter.format(toolCount)} 条</dd></div>
        <div><dt>模型调用</dt><dd>{counter.format(trace.model_calls?.length ?? 0)} 次</dd></div>
      </dl>
      <dl className="obs-trace-models">
        <dt>记录的模型</dt>
        <dd>{models.length ? models.join('、') : '此运行未记录模型'}</dd>
      </dl>
      <div className="obs-trace-actions">
        {conversation && onOpenConversation && (
          <button type="button" className="obs-trace-action obs-trace-action-primary" onClick={() => onOpenConversation(conversation.id, runId)}>
            <ArrowLeftOutlined aria-hidden="true" />返回关联会话
          </button>
        )}
        <button type="button" className="obs-trace-action" aria-expanded={identifiersOpen} aria-controls={identifiersId}
          onClick={() => setIdentifiersOpen(open => !open)}>
          运行标识<RightOutlined className="obs-action-chevron" aria-hidden="true" />
        </button>
      </div>
      <div id={identifiersId} className="obs-trace-identifiers" hidden={!identifiersOpen}>
        <dl className="obs-tool-meta">
          <div><dt>Trace ID</dt><dd><code>{trace.id}</code></dd></div>
          {runId && <div><dt>运行 ID</dt><dd><code>{runId}</code></dd></div>}
          <div><dt>操作名称</dt><dd><code>{trace.operation_name}</code></dd></div>
          {trace.error_code && <div><dt>错误代码</dt><dd><code>{trace.error_code}</code></dd></div>}
        </dl>
      </div>
      {trace.error_message && <Alert type="error" showIcon title={trace.error_message} />}
      <Tabs size="small" tabBarGutter={12} activeKey={activeTab} onChange={onTabChange} items={[
        {
          key: 'tree', label: '运行链路', children: (
            <>
              {runs.map(run => (
                <details className="obs-tree-run" key={run.id} open>
                  <summary className="workbench-disclosure">
                    <strong>{stepLabel(run.agent_name ?? run.workflow_name ?? run.trigger_type, documentRun)}</strong>
                    <small>{duration(run.duration_ms)}</small>
                  </summary>
                  <SpanTree spans={run.spans ?? []} callsBySpan={callsBySpan} documentRun={documentRun} profiles={profiles} />
                </details>
              ))}
              {unlinkedCalls.length > 0 && <TraceToolCalls calls={unlinkedCalls} />}
              {toolCount === 0 && !hasToolSteps && (
                <p className="obs-no-tools">{documentRun && trace.model_calls?.length
                  ? '由模型直接生成资料答复，本次工具调用 0 次。'
                  : '暂无工具调用记录。'}</p>
              )}
            </>
          ),
        },
        { key: 'tools', label: '工具调用', children: <TraceToolCalls calls={trace.tool_calls} /> },
        { key: 'prompts', label: '提示词', children: <TracePrompts calls={trace.model_calls} documentRun={documentRun} /> },
        { key: 'events', label: '事件日志', children: <TraceEvents events={trace.events} spans={spans} documentRun={documentRun} profiles={profiles} /> },
        {
          key: 'io', label: '输入 / 输出', children: (
            <>
              <JsonEvidence label="Trace 输入" value={trace.input_json} />
              <JsonEvidence label="Trace 输出" value={trace.output_json} />
              {trace.model_calls?.map(call => (
                <section className="obs-evidence" key={call.id}>
                  <h3>{call.model} · {stepLabel(call.operation, documentRun)}</h3>
                  <JsonEvidence label="模型输入摘要" value={call.input_summary_json} />
                  <JsonEvidence label="模型输出摘要" value={call.output_summary_json} />
                </section>
              ))}
            </>
          ),
        },
      ]} />
    </div>
  )
}
