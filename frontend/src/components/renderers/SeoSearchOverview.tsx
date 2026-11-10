import {
  AimOutlined,
  DatabaseOutlined,
  InfoCircleOutlined,
  RiseOutlined,
  SearchOutlined,
  TrophyOutlined,
} from '@ant-design/icons'
import { useMemo, type CSSProperties, type ReactNode } from 'react'

import { resolveLocalizedText, type SeoSearchOverviewResultData } from './contracts'

const numberFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })

const metricIcons: Record<string, ReactNode> = {
  organic_traffic: <RiseOutlined />,
  organic_keywords: <SearchOutlined />,
  top_3: <TrophyOutlined />,
  top_10: <AimOutlined />,
}

const integrityLabels: Record<string, string> = {
  complete: '数据完整',
  partial: '部分数据',
  not_available: '数据不足',
}

export function SeoSearchOverview({ data }: { data: SeoSearchOverviewResultData }) {
  const positiveBuckets = useMemo(
    () => data.distribution.filter((item) => item.value > 0),
    [data.distribution],
  )
  const distributionStyle = useMemo<CSSProperties>(
    () => ({
      gridTemplateColumns: positiveBuckets.length
        ? positiveBuckets.map((item) => `${Math.max(item.value, 0.001)}fr`).join(' ')
        : '1fr',
    }),
    [positiveBuckets],
  )
  const distributionLabel = data.distribution
    .map((item) => `${item.label} ${numberFormatter.format(item.value)} 个，占 ${item.percentage}%`)
    .join('；')
  const source = data.provenance.source_label ?? data.provenance.source ?? '未知数据源'
  const integrity = data.provenance.integrity
    ? integrityLabels[data.provenance.integrity.toLowerCase()] ?? data.provenance.integrity
    : undefined

  return (
    <section className="seo-search-overview" data-testid="seo-search-overview" aria-labelledby="seo-search-overview-title">
      <header className="seo-overview-header">
        <div>
          <span className="seo-overview-eyebrow">当前搜索表现</span>
          <h3 id="seo-search-overview-title">{resolveLocalizedText(data.title, '自然搜索概览')}</h3>
          <p>{resolveLocalizedText(data.description, '先看关键结果，再查看关键词明细和执行详情。')}</p>
        </div>
        <span className="seo-overview-source-badge"><DatabaseOutlined /> {source}</span>
      </header>

      <div className="seo-metric-grid">
        {data.metrics.map((metric) => (
          <article className={`seo-metric-card metric-${metric.key}`} key={metric.key}>
            <span className="seo-metric-icon" aria-hidden="true">{metricIcons[metric.key] ?? <InfoCircleOutlined />}</span>
            <div>
              <span className="seo-metric-label">{metric.label}</span>
              <strong>{numberFormatter.format(metric.value)}</strong>
              {metric.helper ? <small>{metric.helper}</small> : null}
            </div>
          </article>
        ))}
      </div>

      <div className="seo-ranking-panel">
        <div className="seo-ranking-heading">
          <div>
            <h4>关键词排名分布</h4>
            <p>Top 10 是累计值，其中已经包含 Top 3。</p>
          </div>
          <strong>{numberFormatter.format(data.metrics.find((item) => item.key === 'organic_keywords')?.value ?? 0)} 个关键词</strong>
        </div>

        {data.warning ? (
          <div className="seo-overview-warning" role="alert"><InfoCircleOutlined /> {data.warning}</div>
        ) : (
          <>
            <div
              className="seo-ranking-bar"
              style={distributionStyle}
              role="img"
              aria-label={distributionLabel}
            >
              {positiveBuckets.map((item) => (
                <span className={`ranking-segment segment-${item.key}`} key={item.key} />
              ))}
            </div>
            <dl className="seo-ranking-legend">
              {data.distribution.map((item) => (
                <div key={item.key}>
                  <dt><span className={`ranking-dot segment-${item.key}`} />{item.label}</dt>
                  <dd><strong>{numberFormatter.format(item.value)}</strong><span>{item.percentage}%</span></dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </div>

      <div className="seo-overview-insight">
        <InfoCircleOutlined aria-hidden="true" />
        <p><strong>这意味着：</strong>{data.insight}</p>
      </div>

      <footer className="seo-overview-meta">
        <span>数据来源：{source}</span>
        {data.provenance.country ? <span>目标市场：{data.provenance.country}</span> : null}
        <span>{data.provenance.is_estimated ? '估算数据' : '实测数据'}</span>
        {integrity ? <span>{integrity}</span> : null}
      </footer>
    </section>
  )
}
