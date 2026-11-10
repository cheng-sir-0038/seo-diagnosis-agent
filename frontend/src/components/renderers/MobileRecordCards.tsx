import { Pagination } from 'antd'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { TableColumnContract } from './contracts'

type MobileRow = Record<string, unknown> & { __toolRowKey: string }

interface MobileRecordCardsProps {
  rows: MobileRow[]
  columns: TableColumnContract[]
  columnLabel: (column: TableColumnContract) => string
  renderCell: (value: unknown, column: TableColumnContract) => ReactNode
  pageSize?: number
}

export function MobileRecordCards({
  rows,
  columns,
  columnLabel,
  renderCell,
  pageSize = 8,
}: MobileRecordCardsProps) {
  const [page, setPage] = useState(1)
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize))

  const safePage = Math.min(page, pageCount)
  const visibleRows = useMemo(
    () => rows.slice((safePage - 1) * pageSize, safePage * pageSize),
    [pageSize, rows, safePage],
  )
  const primary = columns[0]
  const mainMetric = columns[1]
  const details = columns.slice(2)

  return (
    <section className="mobile-record-list" data-testid="mobile-record-list" aria-label="移动端数据记录">
      <header className="mobile-record-list-summary">
        <strong>数据记录</strong>
        <span>共 {rows.length} 条</span>
      </header>

      <div className="mobile-record-cards">
        {visibleRows.map((row) => (
          <article className="mobile-record-card" data-testid="mobile-record-card" key={row.__toolRowKey}>
            <header className="mobile-record-card-header">
              <div className="mobile-record-card-primary">
                {primary ? <span>{columnLabel(primary)}</span> : null}
                <strong>{primary ? renderCell(row[primary.key], primary) : '数据记录'}</strong>
              </div>
              {mainMetric ? (
                <div className="mobile-record-card-metric">
                  <span>{columnLabel(mainMetric)}</span>
                  <strong>{renderCell(row[mainMetric.key], mainMetric)}</strong>
                </div>
              ) : null}
            </header>

            {details.length ? (
              <dl className="mobile-record-card-details">
                {details.map((column) => {
                  const value = row[column.key]
                  const fullWidth = isFullWidthField(column, value)
                  return (
                    <div className={fullWidth ? 'full-width' : undefined} key={column.key}>
                      <dt>{columnLabel(column)}</dt>
                      <dd>{renderCell(value, column)}</dd>
                    </div>
                  )
                })}
              </dl>
            ) : null}
          </article>
        ))}
      </div>

      {rows.length > pageSize ? (
        <Pagination
          className="mobile-record-pagination"
          current={safePage}
          pageSize={pageSize}
          total={rows.length}
          showSizeChanger={false}
          size="small"
          onChange={setPage}
        />
      ) : null}
    </section>
  )
}

function isFullWidthField(column: TableColumnContract, value: unknown): boolean {
  if (column.data_type === 'url' || column.formatter === 'compact_url') return true
  if (/(url|description|title|page|anchor|text)/i.test(column.key)) return true
  return typeof value === 'string' && value.length > 32
}
