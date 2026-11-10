import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DesktopOutlined,
  InfoCircleOutlined,
  MobileOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'

import type { PagePerformanceData, PagePerformanceIssue } from './contracts'

const statusLabels: Record<string, string> = {
  good: '良好',
  needs_improvement: '需要改进',
  poor: '较差',
  unknown: '暂无结果',
}

const scoreIcons: Record<string, ReactNode> = {
  performance: <ThunderboltOutlined />,
  accessibility: <InfoCircleOutlined />,
  'best-practices': <WarningOutlined />,
  seo: <CheckCircleOutlined />,
}

export function PagePerformanceResult({ data }: { data: PagePerformanceData }) {
  const strategy = data.provenance.strategy === 'desktop' ? '桌面端' : '移动端'
  const StrategyIcon = data.provenance.strategy === 'desktop' ? DesktopOutlined : MobileOutlined
  const source = data.provenance.source_label ?? data.provenance.source ?? 'Google PageSpeed Insights'

  return (
    <section
      className="page-performance-result"
      data-testid="page-performance-result"
      aria-label="页面速度诊断结果"
    >
      <div className="page-performance-context">
        <span><StrategyIcon /> {strategy} Lighthouse 检测</span>
        <span>{source}</span>
      </div>

      <div className="pagespeed-score-grid" aria-label="分类评分">
        {data.scores.map((score) => (
          <article className={`pagespeed-score-card status-${score.status}`} key={score.key}>
            <span className="pagespeed-score-icon" aria-hidden="true">
              {scoreIcons[score.key] ?? <InfoCircleOutlined />}
            </span>
            <div>
              <span>{score.label}</span>
              <strong>{score.value ?? '—'}</strong>
              <small>{statusLabels[score.status] ?? score.status}</small>
            </div>
          </article>
        ))}
      </div>

      <section className="pagespeed-section" aria-labelledby="page-performance-metrics-title">
        <div className="pagespeed-section-heading">
          <div>
            <h4 id="page-performance-metrics-title">关键实验室指标</h4>
            <p>反映本次模拟加载中的速度、稳定性和主线程阻塞情况。</p>
          </div>
        </div>
        {data.metrics.length ? (
          <dl className="pagespeed-metric-grid">
            {data.metrics.map((metric) => (
              <div className={`pagespeed-metric status-${metric.status}`} key={metric.key}>
                <dt>{metric.label}</dt>
                <dd>
                  <strong>{metric.display_value}</strong>
                  <span>{statusLabels[metric.status] ?? metric.status}</span>
                </dd>
              </div>
            ))}
          </dl>
        ) : <p className="pagespeed-empty">本次检测没有返回关键性能指标。</p>}
      </section>

      <IssueSection
        id="page-performance-opportunities-title"
        title="优先处理"
        description="按照预计节省时间、资源体积和审计得分排序。"
        items={data.opportunities}
      />

      {data.diagnostics.length ? (
        <IssueSection
          id="page-performance-diagnostics-title"
          title="其他诊断"
          description="这些问题没有明确节省量，但仍可能影响页面质量。"
          items={data.diagnostics}
          compact
        />
      ) : null}

      {data.warnings.length ? (
        <div className="pagespeed-warnings">
          <strong>检测提示</strong>
          <ul>{data.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      ) : null}

      <footer className="pagespeed-report-footer">
        <div className="pagespeed-provenance">
          <span>数据来源：{source}</span>
          <span>检测方式：{strategy}模拟</span>
          {data.provenance.lighthouse_version ? (
            <span>Lighthouse {data.provenance.lighthouse_version}</span>
          ) : null}
          {data.provenance.final_url ? (
            <a href={data.provenance.final_url} target="_blank" rel="noreferrer">
              查看检测页面
            </a>
          ) : null}
        </div>
        <ul className="pagespeed-limitations">
          {data.limitations.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </footer>
    </section>
  )
}

function IssueSection({
  id,
  title,
  description,
  items,
  compact = false,
}: {
  id: string
  title: string
  description: string
  items: PagePerformanceIssue[]
  compact?: boolean
}) {
  return (
    <section className="pagespeed-section" aria-labelledby={id}>
      <div className="pagespeed-section-heading">
        <div><h4 id={id}>{title}</h4><p>{description}</p></div>
        <span>{items.length} 项</span>
      </div>
      {items.length ? (
        <ol className={`pagespeed-opportunity-list${compact ? ' compact' : ''}`}>
          {items.map((item) => (
            <li key={item.audit_id}>
              <div>
                <strong>{item.title}</strong>
                {item.description ? <p>{item.description}</p> : null}
                {item.savings_label ? (
                  <small><ClockCircleOutlined /> {item.savings_label}</small>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : <p className="pagespeed-empty">当前没有返回需要优先处理的自动化建议。</p>}
    </section>
  )
}
