import {
  ApartmentOutlined, CalendarOutlined, ClockCircleOutlined, CloseOutlined, ExclamationCircleOutlined, FileTextOutlined, PullRequestOutlined,
  ReloadOutlined, RightOutlined, SearchOutlined, ToolOutlined, WarningOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Empty, Grid, Input, Select, Switch, Table, Tabs } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { RawJsonResult } from '../components/renderers/RawJsonResult'
import { toolIcon, toolLabel } from '../components/toolLabels'
import { productApi } from '../services/productApi'
import { isSpecialist, specialists } from '../specialists'
import type { Conversation } from '../types/agent'
import type { AgentIssue, MetricSummary, ModelCallRecord, ObservabilityEvent, ObservabilitySpan, ObservabilityTrace, ToolCallRecord } from '../types/product'

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
}
const openStates = new Set(['OPEN', 'REOPENED', 'INVESTIGATING'])
const issueOptions = ['OPEN', 'INVESTIGATING', 'RESOLVED', 'IGNORED', 'ARCHIVED']
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
type TraceResponse = { id: string, trace?: ObservabilityTrace }

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
  if (name === 'workspace.documents') return '资料问答'
  if (name === 'workspace.chat') return '主 Agent 对话'
  if (name === 'seo_agent.chat') return 'SEO / GEO 站点对话'
  return name
}

export function ObservabilityPage({ conversationForRun, onOpenConversation }: {
  conversationForRun?: (runId: string) => ConversationLink | undefined
  onOpenConversation?: (conversationId: string, runId?: string) => void
} = {}) {
  const { xl: wide } = Grid.useBreakpoint()
  const [attempt, setAttempt] = useState(0)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [hours, setHours] = useState(0)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('全部状态')
  const [tab, setTab] = useState('traces')
  const [selectedId, setSelectedId] = useState<string | null>()
  const [detailTab, setDetailTab] = useState<{ traceId: string, tab: string }>()
  const [detailResponse, setDetailResponse] = useState<TraceResponse>()
  const [detailAttempt, setDetailAttempt] = useState(0)
  const [issueAction, setIssueAction] = useState<{ id?: string, error?: string }>({})
  const [data, setData] = useState<{
    traces?: ObservabilityTrace[]
    issues?: AgentIssue[]
    metrics?: MetricSummary[]
    attempt: number
    fetchedAt: number
    incomplete: boolean
  }>()
  const loading = data?.attempt !== attempt

  useEffect(() => {
    const controller = new AbortController()
    const signal = controller.signal
    void Promise.allSettled([
      productApi.listObservabilityTraces(undefined, signal),
      productApi.listAgentIssues(undefined, signal),
      productApi.listMetricSummary(undefined, signal),
    ]).then(([traces, issues, metrics]) => {
      if (signal.aborted) return
      setData(previous => ({
        traces: traces.status === 'fulfilled' ? traces.value : previous?.traces,
        issues: issues.status === 'fulfilled' ? issues.value : previous?.issues,
        metrics: metrics.status === 'fulfilled' ? metrics.value : previous?.metrics,
        attempt,
        fetchedAt: traces.status === 'fulfilled' ? Date.now() : previous?.fetchedAt ?? 0,
        incomplete: traces.status === 'rejected' || issues.status === 'rejected' || metrics.status === 'rejected',
      }))
    })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    if (!autoRefresh || loading) return
    const timer = window.setTimeout(() => setAttempt(count => count + 1), 30_000)
    return () => window.clearTimeout(timer)
  }, [autoRefresh, loading, attempt])

  const timeTraces = useMemo(() => {
    const traces = data?.traces ?? []
    return hours ? traces.filter(trace => Date.parse(trace.started_at) >= (data?.fetchedAt ?? 0) - hours * 3_600_000) : traces
  }, [data, hours])
  const normalizedQuery = query.trim().toLowerCase()
  const linkedConversation = (trace: ObservabilityTrace) => {
    const { application_run_id: runId } = traceMetadata(trace)
    return runId ? conversationForRun?.(runId) : undefined
  }
  const labelForTrace = (trace: ObservabilityTrace) => linkedConversation(trace)?.title ?? operationLabel(trace.operation_name)
  const visibleTraces = timeTraces.filter(trace => {
    const { application_run_id: runId } = traceMetadata(trace)
    const matchesStatus = status === '全部状态' || traceState(trace).text === status
    return matchesStatus && [trace.id, trace.operation_name, labelForTrace(trace), runId]
      .some(value => value?.toLowerCase().includes(normalizedQuery))
  })
  const activeTrace = tab === 'traces'
    ? selectedId === undefined && wide ? visibleTraces[0] : visibleTraces.find(trace => trace.id === selectedId)
    : undefined
  const activeTraceId = activeTrace?.id
  useEffect(() => {
    if (!activeTraceId) return
    const controller = new AbortController()
    productApi.getObservabilityTrace(activeTraceId, controller.signal).then(trace => {
      if (!controller.signal.aborted) setDetailResponse({ id: activeTraceId, trace })
    }).catch(() => {
      if (!controller.signal.aborted) setDetailResponse({ id: activeTraceId })
    })
    return () => controller.abort()
  }, [activeTraceId, attempt, detailAttempt])
  const openIssues = data?.issues?.filter(issue => openStates.has(issue.status))
  const toolCallCount = data?.metrics?.filter(metric => metric.metric_name === 'tool.call.count' && metric.unit === 'calls')
    .reduce((total, metric) => total + metric.point_count, 0)
  const failedCount = data?.traces && timeTraces.filter(trace => trace.status === 'FAILED').length
  const waitingCount = data?.traces && timeTraces.filter(trace => traceMetadata(trace).application_status === 'waiting_for_input').length

  const updateIssue = async (issue: AgentIssue, value: string) => {
    setIssueAction({ id: issue.id })
    try {
      const updated = await productApi.setAgentIssueStatus(issue.id, value)
      setData(previous => previous && { ...previous, issues: previous.issues?.map(item => item.id === updated.id ? updated : item) })
      setIssueAction({})
    } catch {
      setIssueAction({ error: '未能更新问题状态，请重试。' })
    }
  }

  const traceColumns: ColumnsType<ObservabilityTrace> = [
    {
      title: '操作', dataIndex: 'operation_name',
      render: (_, trace) => {
        const label = labelForTrace(trace)
        const runId = traceMetadata(trace).application_run_id ?? trace.id
        return (
          <button type="button" className="observability-trace-button" onClick={() => setSelectedId(trace.id)} aria-pressed={activeTrace?.id === trace.id}>
            <PullRequestOutlined aria-hidden="true" />
            <span><strong title={label}>{label}</strong><small title={runId}>{runId}</small></span>
          </button>
        )
      },
    },
    { title: '状态', width: 96, render: (_, trace) => <TraceState trace={trace} /> },
    { title: '耗时', dataIndex: 'duration_ms', width: 86, align: 'right', render: duration },
    { title: '开始时间', dataIndex: 'started_at', width: 130, render: value => <time className="observability-time" dateTime={value}>{formatTime(value)}</time> },
    { title: '错误', dataIndex: 'error_code', width: 111, ellipsis: true, render: value => value ?? '—' },
  ]

  const issueColumns: ColumnsType<AgentIssue> = [
    { title: '问题', dataIndex: 'title', render: (_, issue) => <strong>{issue.title}</strong> },
    { title: '严重程度', dataIndex: 'severity', width: 112, render: value => <StatusPill value={value} /> },
    { title: '最近出现', dataIndex: 'last_seen_at', width: 150, render: formatTime },
    { title: '次数', dataIndex: 'occurrence_count', width: 74, align: 'right', render: metricValue },
    { title: '状态', dataIndex: 'status', width: 158, render: (_, issue) => (
      <Select
        aria-label={`${issue.title} 的状态`}
        size="small"
        value={issue.status}
        disabled={Boolean(issueAction.id)}
        loading={issueAction.id === issue.id}
        onChange={value => void updateIssue(issue, value)}
        options={Array.from(new Set([issue.status, ...issueOptions])).map(state => ({ value: state, label: state }))}
      />
    ) },
  ]
  const metricColumns: ColumnsType<MetricSummary> = [
    { title: '指标', dataIndex: 'metric_name', width: '32%', render: (name: string) => {
      const label = Object.hasOwn(metricLabels, name) ? metricLabels[name] : undefined
      return <span className="observability-metric-name"><strong>{label ?? name}</strong>{label && <code>{name}</code>}</span>
    } },
    { title: '样本数', dataIndex: 'point_count', width: '13%', align: 'right', render: metricValue },
    { title: '平均值', dataIndex: 'average_value', width: '15%', align: 'right', render: metricValue },
    { title: '最小值', dataIndex: 'minimum_value', width: '15%', align: 'right', render: metricValue },
    { title: '最大值', dataIndex: 'maximum_value', width: '15%', align: 'right', render: metricValue },
    { title: '单位', dataIndex: 'unit', width: '10%', render: value => <span className="observability-metric-unit">{value === 'calls' ? '次' : value ?? '—'}</span> },
  ]

  const detail = activeTrace && (
    <TraceDetail
      key={activeTrace.id}
      response={detailResponse?.id === activeTrace.id ? detailResponse : undefined}
      activeTab={detailTab?.traceId === activeTrace.id ? detailTab.tab : 'tree'}
      onTabChange={tab => setDetailTab({ traceId: activeTrace.id, tab })}
      conversation={linkedConversation(activeTrace)}
      onOpenConversation={onOpenConversation}
      onRetry={() => setDetailAttempt(count => count + 1)}
    />
  )

  return (
    <div className="page-content workspace-observability">
      <PageHeader
        title="运行中心 / 观测面板"
        description="查看运行状态、工具调用、提示词和待处理问题。"
        actions={(
          <>
            <Select
              aria-label="Trace 时间范围"
              prefix={<CalendarOutlined aria-hidden="true" />}
              value={hours}
              onChange={setHours}
              options={[{ value: 0, label: '最近 100 条 Trace' }, { value: 24, label: '最近 24 小时' }, { value: 168, label: '最近 7 天' }]}
            />
            <Button aria-label="刷新观测" icon={<ReloadOutlined />} loading={loading} onClick={() => setAttempt(value => value + 1)} />
            <label className="observability-refresh-switch">自动刷新（30 秒）<Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} aria-label="自动刷新" /></label>
          </>
        )}
      />
      {data?.incomplete && <Alert type="warning" showIcon title="部分观测数据暂时无法读取，刷新后可重试。" />}
      <section className="metric-grid observability-metrics" aria-label="运行指标">
        {([
          ['Trace 记录', data?.traces ? timeTraces.length : undefined, '已加载的 Trace', ApartmentOutlined, 'running'],
          ['失败 Trace', failedCount, '同一 Trace 范围', ExclamationCircleOutlined, 'error'],
          ['工具调用记录', toolCallCount, '数据库累计', ToolOutlined, 'accent'],
          ['等待补充 Trace', waitingCount, '同一 Trace 范围', CalendarOutlined, 'waiting'],
        ] as const).map(([label, value, description, Icon, tone]) => (
          <article className={`metric-card observability-metric-${tone}`} key={label}>
            <Icon className="observability-metric-icon" aria-hidden="true" />
            <div><span>{label}</span><strong>{value === undefined ? '—' : counter.format(value)}</strong><small>{description}</small></div>
          </article>
        ))}
      </section>
      <div className={`observability-layout${wide && activeTrace ? ' with-trace' : ''}`}>
        <section className="workbench-panel observability-table">
          <Tabs activeKey={tab} onChange={setTab} items={[
            {
              key: 'traces', label: 'Trace', children: (
                <>
                  <div className="observability-filter">
                    <Select aria-label="Trace 状态" value={status} onChange={setStatus} options={traceOptions.map(value => ({ value, label: value }))} />
                    <Input prefix={<SearchOutlined aria-hidden="true" />} aria-label="搜索 Trace" placeholder="搜索运行 ID 或操作名称…" value={query} onChange={event => setQuery(event.target.value)} allowClear />
                    <span>{visibleTraces.length} 条记录</span>
                  </div>
                  <Table
                    className="observability-traces-grid"
                    rowKey="id"
                    rowClassName={trace => trace.id === activeTrace?.id ? 'observability-trace-selected' : ''}
                    columns={traceColumns}
                    dataSource={visibleTraces}
                    loading={loading}
                    size="small"
                    scroll={{ x: 610 }}
                    pagination={{ pageSize: 20, hideOnSinglePage: true }}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query || status !== '全部状态' || hours ? '没有匹配的 Trace' : '暂无 Trace'} /> }}
                  />
                </>
              ),
            },
            {
              key: 'issues', label: '问题', children: (
                <>
                  {issueAction.error && <Alert type="error" showIcon title={issueAction.error} />}
                  <Table rowKey="id" columns={issueColumns} dataSource={data?.issues} loading={loading} size="small" scroll={{ x: 660 }} pagination={{ pageSize: 20, hideOnSinglePage: true }} locale={{ emptyText: '暂无聚合问题' }} />
                </>
              ),
            },
            {
              key: 'metrics', label: '指标', children: (
                <Table className="observability-metrics-grid" rowKey={row => `${row.metric_name}:${row.unit ?? ''}`}
                  columns={metricColumns} dataSource={data?.metrics} loading={loading} size="small"
                  tableLayout="fixed" scroll={{ x: 570 }} pagination={false} locale={{ emptyText: '暂无指标' }} />
              ),
            },
          ]} />
          {tab === 'traces' && (
            <section className="observability-issues-preview" aria-label="待处理问题">
              <div className="workbench-panel-header">
                <h2><WarningOutlined aria-hidden="true" />待处理问题</h2>
                <Button type="link" onClick={() => setTab('issues')}>查看全部问题</Button>
              </div>
              {openIssues?.length ? (
                <ul>{openIssues.slice(0, 4).map(issue => <li key={issue.id}><strong>{issue.title}</strong><StatusPill value={issue.severity} /><time dateTime={issue.last_seen_at}>{formatTime(issue.last_seen_at)}</time></li>)}</ul>
              ) : <p className="workbench-empty">{!data?.issues ? (loading ? '正在读取问题…' : '问题数据暂不可用。') : '当前没有待处理的问题。'}</p>}
            </section>
          )}
        </section>
        {wide && activeTrace && (
          <aside className="workbench-panel observability-detail" aria-label="Trace 详情">
            <div className="workbench-panel-header">
              <h2><PullRequestOutlined aria-hidden="true" />Trace 详情</h2>
              <Button type="text" icon={<CloseOutlined />} aria-label="关闭 Trace 详情" onClick={() => setSelectedId(null)} />
            </div>
            {detail}
          </aside>
        )}
      </div>
      {!wide && activeTrace && <Drawer title="Trace 详情" open size="large" onClose={() => setSelectedId(null)}>{detail}</Drawer>}
    </div>
  )
}

function stepLabel(name: string, documentRun: boolean, fallback = '执行步骤') {
  if (/^workspace\.route(?:\.v\d+)?$/.test(name)) return '理解需求并分派专员'
  if (name === 'stream') return documentRun ? '生成资料答复' : '生成答复'
  const iteration = /^model\.iteration\.(\d+)$/.exec(name)
  if (iteration) return documentRun ? '生成资料答复' : `模型处理 · 第 ${iteration[1]} 轮`
  const agent = /^(?:workspace\.|specialist:)(.+)$/.exec(name)?.[1]
  if (isSpecialist(agent)) return `${specialists[agent].name}专员`
  return Object.hasOwn(stepLabels, name) ? stepLabels[name] : fallback
}

function toolTitle(name: string) {
  const label = toolLabel(name)
  return label === name ? '工具调用' : label
}

function spanTitle(span: ObservabilitySpan, documentRun: boolean) {
  const name = span.operation_name
  if (span.kind !== 'TOOL') return stepLabel(name, documentRun, span.kind === 'MODEL' ? '模型处理' : '执行步骤')
  if (name.endsWith('.reused')) return stepLabel(name, documentRun, `复用结果 · ${toolTitle(name.slice(0, -'.reused'.length))}`)
  return toolTitle(name)
}

function SpanTree({ spans, callsBySpan, documentRun }: {
  spans: ObservabilitySpan[]
  callsBySpan: ReadonlyMap<string, ToolCallRecord[]>
  documentRun: boolean
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
      <ul className="observability-tree">
        {nodes.map(span => {
          const calls = callsBySpan.get(span.id)
          return (
            <li key={span.id}>
              {calls ? calls.map(call => <ToolCallDetails key={call.id} call={call} />) : (
                <CallHeader title={spanTitle(span, documentRun)}
                  status={span.status} latency={span.duration_ms}
                  icon={span.kind === 'MODEL' ? FileTextOutlined : span.kind === 'TOOL' ? toolIcon(span.operation_name) : ApartmentOutlined} />
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

function JsonEvidence({ label, value, identifier }: { label: string, value?: string, identifier?: string }) {
  const [open, setOpen] = useState(false)
  if (!value && !identifier) return null
  const data = open ? readJson(value) : undefined
  return (
    <details className="observability-json" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>{label}</summary>
      {identifier && <code className="observability-event-id">{identifier}</code>}
      {open && value && (typeof data === 'string' ? <pre className="json-preview">{data}</pre> : <RawJsonResult data={data} />)}
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
    <span className="observability-call-header">
      <span className="workspace-row-icon"><Icon aria-hidden="true" /></span>
      <span><strong>{title}</strong>{identifier && identifier !== title && <code>{identifier}</code>}</span>
      <span className={`workspace-state workspace-state-${state.tone}`}>{state.text}</span>
      <small>{duration(latency)}</small>
    </span>
  )
}

function ToolCallDetails({ call }: { call: ToolCallRecord }) {
  return (
    <details className="observability-tool-record">
      <summary>
        <CallHeader title={toolTitle(call.tool_name)}
          status={call.status} latency={call.latency_ms} icon={toolIcon(call.tool_name)} />
        <RightOutlined className="observability-tool-chevron" aria-hidden="true" />
      </summary>
      <div className="observability-tool-body">
        <dl className="observability-tool-meta">
          <div><dt>工具标识</dt><dd><code>{call.tool_name}</code></dd></div>
          {call.tool_call_id && <div><dt>调用 ID</dt><dd><code>{call.tool_call_id}</code></dd></div>}
          <div><dt>开始时间</dt><dd>{formatTime(call.started_at)}</dd></div>
          {call.adapter_key && <div><dt>适配器</dt><dd>{call.adapter_key}</dd></div>}
          {call.timeout_seconds != null && <div><dt>超时上限</dt><dd>{call.timeout_seconds} 秒</dd></div>}
          <div><dt>重试次数</dt><dd>{counter.format(call.retry_count ?? 0)}</dd></div>
        </dl>
        {(call.error_message || call.error_code) && (
          <p className="observability-call-error" title={call.error_code ?? undefined}>{call.error_message ?? call.error_code}</p>
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
    <ol className="observability-call-list" aria-label="已记录的工具调用">
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

function TracePrompts({ calls, documentRun }: { calls?: ModelCallRecord[], documentRun: boolean }) {
  if (!calls?.length) return <p className="workbench-empty">暂无已记录的模型调用。</p>
  return (
    <ol className="observability-call-list" aria-label="已记录的模型调用">
      {calls.map(call => {
        const prompts = recordedPrompts(call.input_summary_json)
        const key = call.prompt_key ?? call.operation
        const title = stepLabel(key, documentRun, documentRun ? '生成资料答复' : '模型调用')
        return (
          <li key={call.id}>
            <CallHeader title={title} identifier={call.model}
              status={call.status} latency={call.latency_ms} icon={FileTextOutlined} />
            {(call.prompt_version || call.total_tokens != null) && (
              <div className="observability-call-meta">
                {call.prompt_version && <span title={call.prompt_version_id ?? undefined}>版本 {call.prompt_version}</span>}
                {call.total_tokens != null && <span>{counter.format(call.total_tokens)} tokens</span>}
              </div>
            )}
            {call.error_code && <p className="observability-call-error">{call.error_code}</p>}
            {prompts.length ? prompts.map(({ label, text }, index) => (
              <details className="observability-json" key={`${label}-${index}`}>
                <summary>{label}</summary>
                <pre className="json-preview">{text}</pre>
              </details>
            )) : <p className="observability-prompt-empty">暂无已记录的提示词正文。</p>}
            <details className="observability-json">
              <summary>技术信息</summary>
              <dl className="observability-tool-meta">
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

function TraceEvents({ events, spans, documentRun }: {
  events?: ObservabilityEvent[]
  spans: ObservabilitySpan[]
  documentRun: boolean
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
    <ol className="observability-events" aria-label="运行事件">
      {groups.map(group => {
        const event = group[0]
        const span = event.span_id ? bySpan.get(event.span_id) : undefined
        const payload = readJson(event.payload_json)
        const fields = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
        const subject = event.event_type.startsWith('tool.') && typeof fields.name === 'string' ? toolTitle(fields.name)
          : event.event_type.startsWith('agent.') && isSpecialist(fields.agent) ? specialists[fields.agent].name
            : event.event_type.startsWith('span.') && span ? spanTitle(span, documentRun)
              : event.event_type === 'skill.loaded' && typeof fields.label === 'string' ? fields.label : undefined
        const label = event.event_type === 'tool.completed' && fields.reused === true ? '已复用工具结果'
          : Object.hasOwn(eventLabels, event.event_type) ? eventLabels[event.event_type] : '运行事件'
        const description = [label, subject, group.length > 1 ? `${group.length} 段` : undefined].filter(Boolean).join(' · ')
        const details = group.length > 1
          ? JSON.stringify(group.map(({ sequence, payload_json }) => ({ sequence, payload: readJson(payload_json) })))
          : event.payload_json
        const Icon = event.event_type.startsWith('tool.') && typeof fields.name === 'string' ? toolIcon(fields.name)
          : event.event_type.startsWith('message.') ? FileTextOutlined : ClockCircleOutlined
        return (
          <li key={event.id} data-severity={event.severity}>
            <span className="workspace-row-icon"><Icon aria-hidden="true" /></span>
            <div className="observability-event-copy">
              <strong>{description}</strong>
            </div>
            <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
            <JsonEvidence label="事件数据" value={details} identifier={event.event_type} />
          </li>
        )
      })}
    </ol>
  )
}

function TraceDetail({ response, activeTab, onTabChange, conversation, onOpenConversation, onRetry }: {
  response?: TraceResponse
  activeTab: string
  onTabChange: (tab: string) => void
  conversation?: ConversationLink
  onOpenConversation?: (conversationId: string, runId?: string) => void
  onRetry: () => void
}) {
  if (!response) return <p className="workbench-empty" role="status">正在读取 Trace…</p>
  const { trace } = response
  if (!trace) return (
    <div><p className="workbench-empty" role="alert">暂时无法读取 Trace 详情。</p><Button size="small" onClick={onRetry}>重试</Button></div>
  )
  const { application_run_id: runId } = traceMetadata(trace)
  const documentRun = trace.operation_name === 'workspace.documents'
  const runs = trace.runs ?? []
  const spans = runs.flatMap(run => run.spans ?? [])
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

  return (
    <div className="observability-detail-body">
      <div className="observability-trace-heading">
        <TraceState trace={trace} />
        <strong>{conversation?.title ?? operationLabel(trace.operation_name)}</strong>
      </div>
      <dl className="observability-trace-summary" aria-label="运行摘要">
        <div><dt>开始时间</dt><dd>{formatTime(trace.started_at)}</dd></div>
        <div><dt>总耗时</dt><dd>{duration(trace.duration_ms)}</dd></div>
        <div><dt>工具明细</dt><dd>{counter.format(toolCount)} 条</dd></div>
        <div><dt>模型调用</dt><dd>{counter.format(trace.model_calls?.length ?? 0)} 次</dd></div>
      </dl>
      <div className="observability-trace-actions">
        {conversation && onOpenConversation && <Button size="small" onClick={() => onOpenConversation(conversation.id, runId)}>返回关联会话</Button>}
        <details className="observability-trace-identifiers">
          <summary>运行标识</summary>
          <dl className="observability-tool-meta">
            <div><dt>Trace ID</dt><dd><code>{trace.id}</code></dd></div>
            {runId && <div><dt>运行 ID</dt><dd><code>{runId}</code></dd></div>}
          </dl>
        </details>
      </div>
      {trace.error_message && <Alert type="error" showIcon title={trace.error_message} />}
      <Tabs size="small" tabBarGutter={12} activeKey={activeTab} onChange={onTabChange} items={[
        {
          key: 'tree', label: '运行链路', children: (
            <>
              {runs.map(run => (
                <details className="observability-tree-run" key={run.id} open>
                  <summary>
                    <RightOutlined aria-hidden="true" />
                    <strong>{stepLabel(run.agent_name ?? run.workflow_name ?? run.trigger_type, documentRun)}</strong>
                    <small>{duration(run.duration_ms)}</small>
                  </summary>
                  <SpanTree spans={run.spans ?? []} callsBySpan={callsBySpan} documentRun={documentRun} />
                </details>
              ))}
              {unlinkedCalls.length > 0 && <TraceToolCalls calls={unlinkedCalls} />}
              {toolCount === 0 && !hasToolSteps && (
                <p className="observability-no-tools">{documentRun && trace.model_calls?.length
                  ? '由模型直接生成资料答复，本次工具调用 0 次。'
                  : '暂无工具调用记录。'}</p>
              )}
            </>
          ),
        },
        { key: 'tools', label: '工具调用', children: <TraceToolCalls calls={trace.tool_calls} /> },
        { key: 'prompts', label: '提示词', children: <TracePrompts calls={trace.model_calls} documentRun={documentRun} /> },
        { key: 'events', label: '事件日志', children: <TraceEvents events={trace.events} spans={spans} documentRun={documentRun} /> },
        {
          key: 'io', label: '输入 / 输出', children: (
            <>
              <JsonEvidence label="Trace 输入" value={trace.input_json} />
              <JsonEvidence label="Trace 输出" value={trace.output_json} />
              {trace.model_calls?.map(call => (
                <section className="observability-evidence" key={call.id}>
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
