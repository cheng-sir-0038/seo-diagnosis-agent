import { CheckCircleOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Progress, Skeleton } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { SystemStatus, WorkbenchSummary } from '../types/product'

export function EmployeePage() {
  const { config, site } = useProduct()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    setError(null)
    try {
      const [nextStatus, nextSummary] = await Promise.all([
        productApi.getSystemStatus(),
        productApi.getWorkbench(config.siteId),
      ])
      setStatus(nextStatus)
      setSummary(nextSummary)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])
  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />

  const readiness = [status?.database_started, status?.checkpoint_started].filter(Boolean).length * 50

  return (
    <div className="page-content">
      <PageHeader title="数字员工管理" description="MVP 只运行一个自主 Agent：SEO 优化专员。" actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新状态</Button>} />
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <section className="employee-hero panel-card">
        <div className="employee-avatar"><RobotOutlined /></div>
        <div className="employee-intro">
          <div className="inline-meta"><StatusPill value={status?.database_started && status?.checkpoint_started ? 'READY' : 'DEGRADED'} /><span>单 Agent MVP</span></div>
          <h2>SEO 优化专员</h2>
          <p>为 {site?.domain} 持续发现搜索机会，提交具体动作审批，并跟踪任务、交付、发布、收录和排名效果。</p>
        </div>
        <div className="readiness"><strong>{readiness}%</strong><span>工程基座就绪度</span><Progress percent={readiness} showInfo={false} /></div>
      </section>

      <div className="three-column-grid">
        <section className="panel-card"><h2>职责</h2><ul className="check-list">{['机会扫描与去重', '证据化评分', '具体动作审批', '任务 PRD 与人工派发', '交付验收', '收录与排名监控'].map((item) => <li key={item}><CheckCircleOutlined />{item}</li>)}</ul></section>
        <section className="panel-card"><h2>当前工作</h2><div className="employee-metrics"><div><strong>{summary?.active_scan_runs ?? 0}</strong><span>扫描中</span></div><div><strong>{summary?.pending_approvals ?? 0}</strong><span>待审批</span></div><div><strong>{summary?.active_tasks ?? 0}</strong><span>任务中</span></div><div><strong>{summary?.active_monitoring_plans ?? 0}</strong><span>监控中</span></div></div></section>
        <section className="panel-card"><h2>基础设施</h2><div className="compact-list"><div><span>业务数据库</span><StatusPill value={status?.database_started ? 'READY' : 'UNAVAILABLE'} /></div><div><span>LangGraph Checkpoint</span><StatusPill value={status?.checkpoint_started ? 'READY' : 'UNAVAILABLE'} /></div>{status?.adapters.map((adapter) => <div key={`${adapter.category}:${adapter.key}`}><span>{adapter.display_name}</span><StatusPill value={adapter.implemented ? 'READY' : 'NOT_IMPLEMENTED'} /></div>)}</div>{!status?.adapters.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已注册适配器详情" /> : null}</section>
      </div>

      <section className="panel-card"><div className="section-heading"><div><h2>能力边界</h2><p>诊断与自动修复仍保持协议和伪代码；下列工程链路是真实实现。</p></div></div><div className="capability-grid">{['DataForSEO Tools', 'SQLite 三库', '具体动作审批', 'QQ SMTP', '版本化 PRD', '内部人工派发', 'HTML 发布', '效果监控'].map((item) => <div key={item}><CheckCircleOutlined /><span>{item}</span></div>)}</div></section>
    </div>
  )
}
