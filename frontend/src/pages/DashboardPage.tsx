import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, message, Popconfirm, Skeleton } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useProduct } from '../context/ProductContext'
import { productApi } from '../services/productApi'
import type { ApprovalAction, Opportunity, WorkbenchSummary } from '../types/product'

const emptySummary: WorkbenchSummary = {
  pending_approvals: 0,
  high_value_opportunities: 0,
  medium_opportunities: 0,
  active_tasks: 0,
  active_scan_runs: 0,
  active_monitoring_plans: 0,
  recent_work_records: [],
}

export function DashboardPage() {
  const { config, site } = useProduct()
  const [summary, setSummary] = useState<WorkbenchSummary>(emptySummary)
  const [approvals, setApprovals] = useState<ApprovalAction[]>([])
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!config) return
    setLoading(true)
    setError(null)
    try {
      const [nextSummary, nextApprovals, nextOpportunities] = await Promise.all([
        productApi.getWorkbench(config.siteId),
        productApi.listApprovals(config.siteId),
        productApi.listOpportunities(config.siteId, 'HIGH'),
      ])
      setSummary(nextSummary)
      setApprovals(nextApprovals)
      setOpportunities(nextOpportunities.slice(0, 6))
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

  const decide = async (approval: ApprovalAction, decision: 'APPROVE' | 'REJECT') => {
    if (!config) return
    await productApi.decideApproval(approval.id, {
      actor_id: config.ownerActorId,
      decision,
      reason: decision === 'REJECT' ? '在工作台中拒绝' : undefined,
    })
    message.success(decision === 'APPROVE' ? '已批准该动作' : '已拒绝该动作')
    await load()
  }

  if (loading) return <Skeleton active paragraph={{ rows: 10 }} />

  return (
    <div className="page-content">
      <PageHeader
        title="今日工作台"
        description={`${site?.name ?? '当前站点'} · 聚合机会、审批、任务和最近工作记录`}
        actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
      />
      {error ? <Alert type="error" showIcon message={error} /> : null}

      <section className="metric-grid">
        {[
          ['待决策', summary.pending_approvals, '等待你处理的具体动作'],
          ['高价值机会', summary.high_value_opportunities, '评分 ≥ 80'],
          ['中等机会', summary.medium_opportunities, '评分 60–79'],
          ['进行中任务', summary.active_tasks, '交付、发布或监控中'],
          ['运行中扫描', summary.active_scan_runs, '同站点禁止重叠'],
          ['效果监控', summary.active_monitoring_plans, '收录或排名观察中'],
        ].map(([label, value, help]) => (
          <article className="metric-card" key={String(label)}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{help}</small>
          </article>
        ))}
      </section>

      <div className="dashboard-grid">
        <section className="panel-card span-two">
          <div className="section-heading"><div><h2>待你决策</h2><p>审批只针对当前具体动作，不会一次放行整个机会。</p></div><span>{approvals.length}</span></div>
          {approvals.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审批动作" /> : (
            <div className="approval-list">
              {approvals.map((approval) => (
                <article className="approval-row" key={approval.id}>
                  <div className="approval-icon">!</div>
                  <div className="approval-main">
                    <strong>{approval.action_type}</strong>
                    <p>{approval.target_type} · {approval.target_id}</p>
                    <div className="inline-meta"><StatusPill value={approval.risk_level} /><span>{new Date(approval.requested_at).toLocaleString()}</span></div>
                  </div>
                  <div className="approval-actions">
                    <Button type="primary" icon={<CheckOutlined />} onClick={() => void decide(approval, 'APPROVE')}>批准</Button>
                    <Popconfirm title="确认拒绝该动作？" onConfirm={() => void decide(approval, 'REJECT')}>
                      <Button danger icon={<CloseOutlined />}>拒绝</Button>
                    </Popconfirm>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="panel-card">
          <div className="section-heading"><div><h2>高价值机会</h2><p>只展示真实入库机会。</p></div></div>
          {opportunities.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待诊断引擎产生结构化候选" /> : (
            <div className="opportunity-mini-list">
              {opportunities.map((opportunity) => (
                <div className="opportunity-mini" key={opportunity.id}>
                  <div className="score-orb">{opportunity.current_score}</div>
                  <div><strong>{opportunity.title}</strong><p>{opportunity.current_summary}</p><StatusPill value={opportunity.status} /></div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel-card span-three">
          <div className="section-heading"><div><h2>最近工作记录</h2><p>来源于追加式业务记录，不从 checkpoint 或模型临时生成。</p></div></div>
          {summary.recent_work_records.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工作记录" /> : (
            <div className="work-timeline">
              {summary.recent_work_records.map((record) => (
                <article key={record.id}>
                  <time>{new Date(record.occurred_at).toLocaleString()}</time>
                  <span />
                  <div><strong>{record.title}</strong><p>{record.summary}</p></div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
