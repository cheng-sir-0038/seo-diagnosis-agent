import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'

import { ResultTag, type ResultTagTone } from '../ResultTag'
import { resolveLocalizedText, type PageMetadataData, type PageMetadataStatus } from './contracts'

const statusLabels: Record<PageMetadataStatus, string> = {
  good: '已设置',
  warning: '需要确认',
  missing: '未设置',
  blocked: '阻止收录',
  info: '信息项',
}

const statusIcons: Record<PageMetadataStatus, ReactNode> = {
  good: <CheckCircleOutlined />,
  warning: <WarningOutlined />,
  missing: <CloseCircleOutlined />,
  blocked: <CloseCircleOutlined />,
  info: <InfoCircleOutlined />,
}

const statusTones: Record<PageMetadataStatus, ResultTagTone> = {
  good: 'positive',
  warning: 'warning',
  missing: 'danger',
  blocked: 'danger',
  info: 'neutral',
}

const checkOrder: Partial<Record<string, number>> = { title: 0, h1: 1 }

export function PageMetadataResult({ data }: { data: PageMetadataData }) {
  const source = data.provenance.source_label ?? data.provenance.source ?? '页面源 HTML'
  const url = data.http.final_url ?? data.provenance.final_url
  const checks = [...data.checks].sort((left, right) => (checkOrder[left.key] ?? 2) - (checkOrder[right.key] ?? 2))
  const statusOk = data.http.status_code >= 200 && data.http.status_code < 300
  const indexingStatus: PageMetadataStatus = data.indexing.declared_noindex
    ? 'blocked'
    : statusOk ? 'good' : 'warning'

  return (
    <section className="page-metadata-result" data-testid="page-metadata-result" aria-label="页面元信息检查结果">
      <header className="page-metadata-context">
        <span className="page-metadata-icon" aria-hidden="true"><FileSearchOutlined /></span>
        <div>
          <h3>{resolveLocalizedText(data.title, '页面元信息检查')}</h3>
          {url ? (
            <a href={url} target="_blank" rel="noreferrer" aria-label={`打开页面 ${url}`}>
              {url} <LinkOutlined aria-hidden="true" />
            </a>
          ) : <span>当前页面</span>}
        </div>
        <div className="inline-result-tags">
          <ResultTag icon={<CodeOutlined />} tone="source">{source}</ResultTag>
          <ResultTag icon={statusIcons[statusOk ? 'good' : 'warning']} tone={statusOk ? 'positive' : 'warning'}>
            HTTP {data.http.status_code}
          </ResultTag>
        </div>
      </header>

      <div className="metadata-report-body">
        <dl className="metadata-facts">
          {checks.map((check) => (
            <div className="metadata-fact" key={check.key}>
              <dt>{check.label}</dt>
              <dd className="metadata-fact-value">
                <span className={check.value ? undefined : 'metadata-empty-value'}>{check.value || '暂无内容'}</span>
                {check.value || check.count > 1 || check.note ? (
                  <small className="metadata-check-meta">
                    {check.value ? <span>{check.length} 个字符</span> : null}
                    {check.count > 1 ? <span>{check.count} 个重复声明</span> : null}
                    {check.note ? <span>{check.note}</span> : null}
                  </small>
                ) : null}
              </dd>
              <dd className="metadata-fact-status">
                <ResultTag icon={statusIcons[check.status]} tone={statusTones[check.status]}>
                  {statusLabels[check.status]}
                </ResultTag>
              </dd>
            </div>
          ))}
        </dl>

        <details className={`metadata-detail-panel status-${indexingStatus}`}>
          <summary className="workbench-disclosure">
            <span className="metadata-indexing-icon" aria-hidden="true">{statusIcons[indexingStatus]}</span>
            <span className="metadata-indexing-label">
              <strong>{data.indexing.declared_noindex ? '页面声明了 noindex' : '未发现 noindex 声明'}</strong>
              <span>索引控制与页面结构</span>
            </span>
          </summary>
          <div className="metadata-detail-body">
            <p>{data.indexing.declared_noindex
              ? '搜索引擎可能不会收录该页面。'
              : '实际收录状态需要另外确认。'}</p>
            <dl>
              <div><dt>Robots</dt><dd>{data.indexing.robots || '未声明，默认允许索引与跟踪'}</dd></div>
              {data.indexing.googlebot ? <div><dt>Googlebot</dt><dd>{data.indexing.googlebot}</dd></div> : null}
              {data.indexing.x_robots_tag ? <div><dt>X-Robots-Tag</dt><dd>{data.indexing.x_robots_tag}</dd></div> : null}
              <div><dt>H1 数量</dt><dd>{data.headings.length}</dd></div>
              {data.additional.charset ? <div><dt>字符编码</dt><dd>{data.additional.charset}</dd></div> : null}
              {data.http.content_type ? <div><dt>Content-Type</dt><dd>{data.http.content_type}</dd></div> : null}
            </dl>
            {data.headings.length ? (
              <div className="metadata-heading-list">
                {data.headings.map((heading, index) => <span key={`${heading}-${index}`}>H1 {index + 1}：{heading}</span>)}
              </div>
            ) : null}
            {data.limitations.length ? (
              <ul className="metadata-limitations">{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            ) : null}
          </div>
        </details>

        {data.analyzer && data.analyzer.status !== 'disabled' ? (
          <section className={`metadata-analyzer-panel status-${data.analyzer.status}`} aria-label="页面基础扫描结果">
            <div className="metadata-analyzer-heading">
              <div>
                <span className="page-metadata-eyebrow"><FileSearchOutlined /> python-seo-analyzer 补充证据</span>
                <strong>{data.analyzer.status === 'completed' ? '基础 On-page 扫描已完成' : '基础 On-page 扫描不可用'}</strong>
              </div>
              {data.analyzer.version ? <small>v{data.analyzer.version}</small> : null}
            </div>
            {data.analyzer.status === 'completed' ? (
              <>
                <div className="metadata-analyzer-metrics">
                  <span><strong>{data.analyzer.word_count ?? '—'}</strong>正文词数</span>
                  <span><strong>{data.analyzer.internal_links_count ?? '—'}</strong>站内链接</span>
                  <span><strong>{data.analyzer.image_missing_alt_count ?? '—'}</strong>图片缺少 alt</span>
                  <span><strong>{Object.values(data.analyzer.heading_counts).reduce((total, value) => total + value, 0)}</strong>H1-H6 标题</span>
                </div>
                {data.analyzer.warnings.length ? (
                  <div className="metadata-analyzer-observations">
                    <small>以下是第三方工具原始观察，不会直接判定为正式 SEO 问题：</small>
                    <ul>{data.analyzer.warnings.slice(0, 5).map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                ) : null}
              </>
            ) : (
              <p>{data.analyzer.error || '隔离分析环境当前不可用，页面标签检查结果仍然有效。'}</p>
            )}
          </section>
        ) : null}

        {data.warnings.length ? (
          <section className="metadata-warning-list">
            <h4><WarningOutlined /> 需要注意</h4>
            <ul>{data.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        ) : null}
      </div>
    </section>
  )
}
