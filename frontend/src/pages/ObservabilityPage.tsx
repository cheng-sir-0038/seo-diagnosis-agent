import { ReloadOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, Select, Table, Tabs, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { productApi } from '../services/productApi'
import type {
  AgentIssue,
  MetricSummary,
  ObservabilityEvent,
  ObservabilitySpan,
  ObservabilityTrace,
} from '../types/product'

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function safeJson(value: string) {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

export function ObservabilityPage() {
  const [traces, setTraces] = useState<ObservabilityTrace[]>([])
  const [issues, setIssues] = useState<AgentIssue[]>([])
  const [metrics, setMetrics] = useState<MetricSummary[]>([])
  const [selected, setSelected] = useState<ObservabilityTrace | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [nextTraces, nextIssues, nextMetrics] = await Promise.all([
        productApi.listObservabilityTraces(),
        productApi.listAgentIssues(),
        productApi.listMetricSummary(),
      ])
      setTraces(nextTraces)
      setIssues(nextIssues)
      setMetrics(nextMetrics)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const openTrace = async (trace: ObservabilityTrace) => {
    setSelected(await productApi.getObservabilityTrace(trace.id))
  }

  const updateIssue = async (issue: AgentIssue, status: string) => {
    await productApi.setAgentIssueStatus(issue.id, status)
    message.success(`问题状态已更新为 ${status}`)
    await load()
  }

  const summary = useMemo(
    () => ({
      total: traces.length,
      failed: traces.filter((trace) => trace.status === 'FAILED').length,
      openIssues: issues.filter((issue) => ['OPEN', 'REOPENED', 'INVESTIGATING'].includes(issue.status)).length,
      toolCalls: metrics.find((metric) => metric.metric_name === 'tool.call.count')?.point_count ?? 0,
    }),
    [traces, issues, metrics],
  )

  const traceColumns: ColumnsType<ObservabilityTrace> = [
    {
      title: 'Trace',
      dataIndex: 'operation_name',
      render: (_, trace) => (
        <button className="link-button" onClick={() => void openTrace(trace)}>
          <strong>{trace.operation_name}</strong>
          <small>{trace.id}</small>
        </button>
      ),
    },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> },
    { title: '耗时', dataIndex: 'duration_ms', width: 110, render: (value) => value == null ? '—' : `${Number(value).toFixed(1)} ms` },
    { title: '开始时间', dataIndex: 'started_at', width: 190, render: formatTime },
    { title: '错误', dataIndex: 'error_code', width: 180, render: (value) => value ?? '—' },
  ]

  const issueColumns: ColumnsType<AgentIssue> = [
    { title: '问题', dataIndex: 'title', render: (_, issue) => <div><strong>{issue.title}</strong><small>{issue.component ?? issue.issue_type}</small></div> },
    { title: '严重度', dataIndex: 'severity', width: 110, render: (value) => <StatusPill value={value} /> },
    { title: '状态', dataIndex: 'status', width: 170, render: (_, issue) => (
      <Select
        value={issue.status}
        style={{ width: 145 }}
        onChange={(value) => void updateIssue(issue, value)}
        options={['OPEN', 'INVESTIGATING', 'RESOLVED', 'IGNORED', 'ARCHIVED'].map((value) => ({ value, label: value }))}
      />
    ) },
    { title: '次数', dataIndex: 'occurrence_count', width: 90 },
    { title: '影响 Trace', dataIndex: 'affected_trace_count', width: 110 },
    { title: '最近出现', dataIndex: 'last_seen_at', width: 190, render: formatTime },
  ]

  const metricColumns: ColumnsType<MetricSummary> = [
    { title: '指标', dataIndex: 'metric_name' },
    { title: '点数', dataIndex: 'point_count', width: 90 },
    { title: '平均值', dataIndex: 'average_value', width: 130, render: (value) => Number(value).toFixed(3) },
    { title: '最小值', dataIndex: 'minimum_value', width: 130, render: (value) => Number(value).toFixed(3) },
    { title: '最大值', dataIndex: 'maximum_value', width: 130, render: (value) => Number(value).toFixed(3) },
    { title: '单位', dataIndex: 'unit', width: 100, render: (value) => value ?? '—' },
  ]

  return (
    <div className="page-content">
      <PageHeader
        title="运行观测"
        description="Trace、Run、Span、模型、工具、问题和指标使用统一协议持久化。"
        actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
      />
      <section className="metric-grid observability-metrics">
        {[
          ['Trace', summary.total, '当前查询窗口'],
          ['失败 Trace', summary.failed, '需要排查'],
          ['开放问题', summary.openIssues, '含重新打开'],
          ['工具调用点', summary.toolCalls, '已持久化调用'],
        ].map(([label, value, help]) => (
          <article className="metric-card" key={String(label)}><span>{label}</span><strong>{value}</strong><small>{help}</small></article>
        ))}
      </section>
      <section className="panel-card table-card">
        <Tabs
          items={[
            { key: 'traces', label: `Trace ${traces.length}`, children: <Table rowKey="id" columns={traceColumns} dataSource={traces} loading={loading} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无 Trace" /> }} /> },
            { key: 'issues', label: `问题 ${issues.length}`, children: <Table rowKey="id" columns={issueColumns} dataSource={issues} loading={loading} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无聚合问题" /> }} /> },
            { key: 'metrics', label: `指标 ${metrics.length}`, children: <Table rowKey={(row) => `${row.metric_name}:${row.unit ?? ''}`} columns={metricColumns} dataSource={metrics} loading={loading} pagination={false} locale={{ emptyText: <Empty description="暂无指标" /> }} /> },
          ]}
        />
      </section>
      <Drawer title="Trace 详情" size="large" open={Boolean(selected)} onClose={() => setSelected(null)}>
        {selected ? <TraceDetail trace={selected} /> : null}
      </Drawer>
    </div>
  )
}

function TraceDetail({ trace }: { trace: ObservabilityTrace }) {
  const spans = trace.runs?.flatMap((run) => run.spans ?? []) ?? []
  const spanColumns: ColumnsType<ObservabilitySpan> = [
    { title: '操作', dataIndex: 'operation_name' },
    { title: '类型', dataIndex: 'kind', width: 160 },
    { title: '组件', dataIndex: 'component_name', width: 160, render: (value) => value ?? '—' },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <StatusPill value={value} /> },
    { title: '耗时', dataIndex: 'duration_ms', width: 110, render: (value) => value == null ? '—' : `${Number(value).toFixed(1)} ms` },
  ]
  const eventColumns: ColumnsType<ObservabilityEvent> = [
    { title: '#', dataIndex: 'sequence', width: 70 },
    { title: '事件', dataIndex: 'event_type', width: 220 },
    { title: '阶段', dataIndex: 'stage', width: 160, render: (value) => value ?? '—' },
    { title: '载荷', dataIndex: 'payload_json', render: (value) => <pre className="inline-json">{safeJson(value)}</pre> },
  ]
  return (
    <div className="detail-stack">
      <div className="detail-toolbar"><StatusPill value={trace.status} /><span>{trace.id}</span><span>{trace.duration_ms?.toFixed(1) ?? '—'} ms</span></div>
      {trace.error_message ? <section><h3>根错误</h3><pre className="json-preview">{trace.error_code}: {trace.error_message}</pre></section> : null}
      <section><h3>Span 树</h3><Table rowKey="id" columns={spanColumns} dataSource={spans} pagination={false} size="small" /></section>
      <section><h3>模型调用</h3><pre className="json-preview">{JSON.stringify(trace.model_calls ?? [], null, 2)}</pre></section>
      <section><h3>工具调用</h3><pre className="json-preview">{JSON.stringify(trace.tool_calls ?? [], null, 2)}</pre></section>
      <section><h3>事件</h3><Table rowKey="id" columns={eventColumns} dataSource={trace.events ?? []} pagination={false} size="small" /></section>
    </div>
  )
}
