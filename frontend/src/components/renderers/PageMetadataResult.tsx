import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'

import type { PageMetadataData, PageMetadataStatus } from './contracts'

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

export function PageMetadataResult({ data }: { data: PageMetadataData }) {
  const source = data.provenance.source_label ?? data.provenance.source ?? '页面源 HTML'
  const statusOk = data.http.status_code >= 200 && data.http.status_code < 300
  const indexingStatus: PageMetadataStatus = data.indexing.declared_noindex
    ? 'blocked'
    : statusOk ? 'good' : 'warning'

  return (
    <section className="page-metadata-result" data-testid="page-metadata-result" aria-label="页面元信息检查结果">
      <header className="page-metadata-context">
        <div>
          <span className="page-metadata-eyebrow"><FileSearchOutlined /> 直接读取页面 HTML</span>
          <strong>{data.http.final_url ?? data.provenance.final_url ?? '当前页面'}</strong>
        </div>
        <span className={`metadata-http-status status-${statusOk ? 'good' : 'warning'}`}>
          HTTP {data.http.status_code}
        </span>
      </header>

      <div className={`metadata-indexing-banner status-${indexingStatus}`}>
        {statusIcons[indexingStatus]}
        <div>
          <strong>{data.indexing.declared_noindex ? '页面声明了 noindex' : '未发现 noindex 声明'}</strong>
          <span>{data.indexing.declared_noindex
            ? '搜索引擎可能不会收录该页面。'
            : '这只表示页面标签没有主动禁止收录，不代表页面一定会被收录。'}</span>
        </div>
      </div>

      <div className="metadata-check-grid">
        {data.checks.map((check) => (
          <article className={`metadata-check-card status-${check.status}`} key={check.key}>
            <div className="metadata-check-heading">
              <span aria-hidden="true">{statusIcons[check.status]}</span>
              <strong>{check.label}</strong>
              <small>{statusLabels[check.status]}</small>
            </div>
            <p className={check.value ? undefined : 'metadata-empty-value'}>
              {check.value || '暂无内容'}
            </p>
            <div className="metadata-check-meta">
              {check.value ? <span>{check.length} 个字符</span> : null}
              {check.count > 1 ? <span>{check.count} 个重复声明</span> : null}
            </div>
            {check.note ? <small className="metadata-check-note">{check.note}</small> : null}
          </article>
        ))}
      </div>

      <section className="metadata-detail-panel" aria-label="索引控制信息">
        <h4>索引控制与页面结构</h4>
        <dl>
          <div><dt>Robots</dt><dd>{data.indexing.robots || '未声明，默认允许索引与跟踪'}</dd></div>
          {data.indexing.googlebot ? <div><dt>Googlebot</dt><dd>{data.indexing.googlebot}</dd></div> : null}
          {data.indexing.x_robots_tag ? <div><dt>X-Robots-Tag</dt><dd>{data.indexing.x_robots_tag}</dd></div> : null}
          <div><dt>H1 数量</dt><dd>{data.headings.length}</dd></div>
          {data.additional.charset ? <div><dt>字符编码</dt><dd>{data.additional.charset}</dd></div> : null}
        </dl>
        {data.headings.length ? (
          <div className="metadata-heading-list">
            {data.headings.map((heading, index) => <span key={`${heading}-${index}`}>H1 {index + 1}：{heading}</span>)}
          </div>
        ) : null}
      </section>

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

      <footer className="metadata-result-footer">
        <div>
          <span>数据来源：{source}</span>
          {data.http.content_type ? <span>{data.http.content_type}</span> : null}
          {data.http.final_url ? (
            <a href={data.http.final_url} target="_blank" rel="noreferrer"><LinkOutlined /> 打开页面</a>
          ) : null}
        </div>
        <ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      </footer>
    </section>
  )
}
