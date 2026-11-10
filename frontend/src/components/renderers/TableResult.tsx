import { QuestionCircleOutlined } from '@ant-design/icons'
import { Table, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { MobileRecordCards } from './MobileRecordCards'
import { RawJsonResult } from './RawJsonResult'
import { isTableResultData, resolveLocalizedText } from './contracts'
import type {
  PresentationRange,
  TableColumnContract,
  TableDataType,
  TableResultData,
} from './contracts'

type DisplayRow = Record<string, unknown> & { __toolRowKey: string }

const integerFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })
const decimalFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const dateFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })

export function TableResult({ data, rawData = data }: { data: unknown; rawData?: unknown }) {
  const mobile = useMobileTableLayout()
  if (!isTableResultData(data)) return <RawJsonResult data={rawData} />
  const rows = buildDisplayRows(data.rows)
  const columns: ColumnsType<DisplayRow> = data.columns.map((column, index) => ({
    title: columnTitle(column),
    dataIndex: column.key,
    key: column.key,
    ellipsis: true,
    align: column.align,
    width: column.width,
    fixed: index === 0 ? 'left' : undefined,
    sorter: column.sortable
      ? (left, right) => compareValues(left[column.key], right[column.key], column.data_type)
      : undefined,
    render: (value: unknown): ReactNode => renderValue(value, column),
  }))

  return (
    <div
      className={`tool-table-result${mobile ? ' mobile-layout' : ' desktop-layout'}`}
      data-testid="table-result"
    >
      {mobile ? (
        <MobileRecordCards
          rows={rows}
          columns={data.columns}
          columnLabel={columnLabel}
          renderCell={renderValue}
        />
      ) : (
        <>
          <div className="presentation-scroll-hint">左右滑动查看完整指标</div>
          <Table<DisplayRow>
            size="small"
            columns={columns}
            dataSource={rows}
            rowKey="__toolRowKey"
            pagination={rows.length > 8 ? { pageSize: 8, size: 'small' } : false}
            scroll={{ x: 'max-content' }}
          />
        </>
      )}
      <PresentationFooter data={data} />
    </div>
  )
}

function columnTitle(column: TableColumnContract): ReactNode {
  const label = columnLabel(column)
  if (!column.help_text) return label
  return (
    <span className="presentation-column-title">
      {label}
      <Tooltip title={column.help_text}>
        <QuestionCircleOutlined aria-label={`${label}说明`} />
      </Tooltip>
    </span>
  )
}

function columnLabel(column: TableColumnContract): string {
  return resolveLocalizedText(column.label, column.title ?? column.key)
}

function useMobileTableLayout(): boolean {
  const query = '(max-width: 640px)'
  const [mobile, setMobile] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(query).matches
  ))

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(query)
    const update = (event: MediaQueryListEvent | MediaQueryList) => setMobile(event.matches)
    update(media)
    media.addEventListener?.('change', update)
    return () => media.removeEventListener?.('change', update)
  }, [])

  return mobile
}

function renderValue(value: unknown, column: TableColumnContract): ReactNode {
  if (isEmpty(value)) return <span className="presentation-null">{column.null_text ?? '暂无数据'}</span>

  switch (column.formatter) {
    case 'rank_delta':
      return rankDelta(value)
    case 'difficulty':
      return difficulty(value, column.ranges ?? [])
    case 'enum':
      return column.value_map?.[String(value)] ?? displayText(value)
    case 'compact_url':
      return compactUrl(value)
    case 'integer':
      return integerFormatter.format(numeric(value))
    case 'decimal':
      return decimalFormatter.format(numeric(value))
    case 'percentage': {
      const number = numeric(value)
      const percentage = Math.abs(number) <= 1 ? number * 100 : number
      return `${decimalFormatter.format(percentage)}%`
    }
    case 'date':
      return formatDate(value)
    case 'boolean_status': {
      const booleanValue = value === true || value === 'true' || value === 1
      const key = String(booleanValue)
      return column.value_map?.[key] ?? (booleanValue ? '是' : '否')
    }
    default:
      if (column.data_type === 'url') return compactUrl(value)
      if (column.data_type === 'number') return decimalFormatter.format(numeric(value))
      return displayText(value)
  }
}

function rankDelta(value: unknown): ReactNode {
  const delta = numeric(value)
  if (delta === 0) return <span className="rank-delta rank-same">持平</span>
  if (delta > 0) return <span className="rank-delta rank-up">上升 {integerFormatter.format(delta)} 位</span>
  return <span className="rank-delta rank-down">下降 {integerFormatter.format(Math.abs(delta))} 位</span>
}

function difficulty(value: unknown, ranges: PresentationRange[]): ReactNode {
  const number = numeric(value)
  const matched = ranges.find((range) => (
    (range.min === undefined || number >= range.min)
    && (range.max === undefined || number <= range.max)
  ))
  return matched ? `${decimalFormatter.format(number)} · ${matched.label}` : decimalFormatter.format(number)
}

function compactUrl(value: unknown): ReactNode {
  const full = String(value)
  if (!/^https?:\/\//.test(full)) return displayText(value)
  try {
    const parsed = new URL(full)
    const path = parsed.pathname === '/' ? '/' : parsed.pathname.replace(/\/$/, '')
    const label = `${parsed.hostname}${path}`
    return <a href={full} target="_blank" rel="noreferrer" title={full}>{label}</a>
  } catch {
    return <a href={full} target="_blank" rel="noreferrer">{full}</a>
  }
}

function PresentationFooter({ data }: { data: TableResultData }) {
  const provenance = data.provenance
  const limitations = data.limitations ?? []
  if (!provenance && limitations.length === 0) return null
  return (
    <footer className="presentation-table-footer">
      {provenance ? (
        <div className="presentation-provenance">
          {provenance.source_label || provenance.source ? (
            <span>数据来源：{provenance.source_label ?? provenance.source}</span>
          ) : null}
          <span>{provenance.is_estimated ? '估算数据' : '实测数据'}</span>
          {provenance.country ? <span>目标市场：{provenance.country}</span> : null}
        </div>
      ) : null}
      {limitations.length > 0 ? (
        <ul className="presentation-limitations">
          {limitations.map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : null}
    </footer>
  )
}

function compareValues(left: unknown, right: unknown, type: TableDataType = 'string'): number {
  if (type === 'number') return numeric(left, Number.NEGATIVE_INFINITY) - numeric(right, Number.NEGATIVE_INFINITY)
  if (type === 'date') return dateValue(left) - dateValue(right)
  if (type === 'boolean') return Number(Boolean(left)) - Number(Boolean(right))
  return displayText(left).localeCompare(displayText(right), 'zh-CN', { numeric: true })
}

function displayText(value: unknown): string {
  if (isEmpty(value)) return '暂无数据'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function numeric(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function dateValue(value: unknown): number {
  const parsed = Date.parse(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function formatDate(value: unknown): string {
  const parsed = new Date(String(value))
  return Number.isFinite(parsed.getTime()) ? dateFormatter.format(parsed) : displayText(value)
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === ''
}

const ROW_IDENTITY_FIELDS = [
  'keyword',
  'domain',
  'source_url',
  'target_url',
  'url',
  'title',
  'page',
  'anchor_text',
  'rank',
  'position',
] as const

function buildDisplayRows(rows: Record<string, unknown>[]): DisplayRow[] {
  const occurrences = new Map<string, number>()
  return rows.map((row, index) => {
    const identity = stableRowIdentity(row, index)
    const occurrence = (occurrences.get(identity) ?? 0) + 1
    occurrences.set(identity, occurrence)
    return {
      ...row,
      __toolRowKey: occurrence === 1 ? identity : `${identity}#${occurrence}`,
    }
  })
}

function stableRowIdentity(row: Record<string, unknown>, index: number): string {
  for (const key of ['key', 'id'] as const) {
    const value = primitiveIdentityValue(row[key])
    if (value !== null) return `${key}:${value}`
  }

  const businessParts = ROW_IDENTITY_FIELDS.flatMap((key) => {
    const value = primitiveIdentityValue(row[key])
    return value === null ? [] : [[key, value]]
  })
  if (businessParts.length > 0) return `composite:${JSON.stringify(businessParts)}`

  const primitiveParts = Object.entries(row)
    .flatMap(([key, raw]) => {
      const value = primitiveIdentityValue(raw)
      return value === null ? [] : [[key, value]]
    })
    .sort(([left], [right]) => left.localeCompare(right))
  if (primitiveParts.length > 0) return `row:${JSON.stringify(primitiveParts)}`
  return `row:${index}`
}

function primitiveIdentityValue(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return String(value)
  return null
}
