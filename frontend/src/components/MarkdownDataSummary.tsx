import { BarChartOutlined } from '@ant-design/icons'

import type { MarkdownDataSummaryData } from './markdownDataSummaryParser'

export function MarkdownDataSummary({ data }: { data: MarkdownDataSummaryData }) {
  return (
    <section
      className="markdown-data-summary"
      data-testid="markdown-data-summary"
      role="region"
      aria-label={data.title}
    >
      <header className="markdown-data-summary-header">
        <span className="markdown-data-summary-icon" aria-hidden="true">
          <BarChartOutlined />
        </span>
        <div>
          <span>数据摘要</span>
          <h4>{data.title}</h4>
        </div>
      </header>

      <div className="markdown-data-summary-rows">
        {data.rows.map((row) => (
          <article
            className="markdown-data-summary-row"
            key={`${row.label}-${row.displayValue}`}
          >
            <div className="markdown-data-summary-row-head">
              <strong>{row.label}</strong>
              <span>{row.displayValue}</span>
            </div>
            <div className="markdown-data-summary-bar" aria-hidden="true">
              <span
                style={{
                  width: `${row.percentage}%`,
                  minWidth: row.percentage > 0 ? '3px' : '0px',
                }}
              />
            </div>
            {(row.percentageLabel || row.note) ? (
              <div className="markdown-data-summary-row-foot">
                {row.percentageLabel ? <span>{row.percentageLabel}</span> : null}
                {row.note ? <p>{row.note}</p> : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  )
}
