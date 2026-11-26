import { BankOutlined, ClockCircleOutlined, ShoppingOutlined, UnorderedListOutlined } from '@ant-design/icons'

import { ResultTag } from '../ResultTag'
import { CardResult } from './CardResult'
import type { CardMetadataItem, CardResultData, CardResultItem } from './contracts'
import './ProcurementResult.css'

const sections = [
  {
    label: '采购需求', heading: '物品', keyPattern: /^line-/, icon: UnorderedListOutlined,
    columns: [
      { title: '需求规格', fields: ['需求规格'] },
      { title: '需求数量', fields: ['需求数量', '数量估算'], numeric: true },
    ],
  },
  {
    label: '商家报价', heading: '商品与商家', keyPattern: /^quote-/, icon: BankOutlined, quote: true,
    columns: [
      { title: '规格与起订', fields: ['报价规格', '单位与每包数量', '起订'] },
      { title: '采购数量', fields: ['需求数量', '实买数量'], numeric: true },
      { title: '费用', fields: ['总费用', '已知小计', '适用价'], numeric: true },
    ],
  },
  {
    label: '商品候选', heading: '商品', keyPattern: /^source-\d+-\d+$/, icon: ShoppingOutlined, catalog: true,
    columns: [
      { title: '来源与店铺', fields: ['来源', '店铺', '查询时间', '有效期', '剩余量'] },
      { title: '参考价格', fields: ['目录参考价', '平台预估价', '商品标价', '券面金额', '使用门槛'], numeric: true },
    ],
  },
]

const shanghaiTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hourCycle: 'h23',
})
const columnFieldLabels: Record<string, string> = { 报价规格: '规格', 单位与每包数量: '包装' }

function MetadataValue({ entry }: { entry: CardMetadataItem }) {
  if (entry.value === '待明确' || entry.value === '待核对') {
    return <ResultTag icon={<ClockCircleOutlined />} tone="warning">{entry.value}</ResultTag>
  }
  if (entry.label === '查询时间') {
    const date = new Date(entry.value)
    if (Number.isFinite(date.getTime())) return <time dateTime={entry.value}>{shanghaiTime.format(date)}</time>
  }
  return entry.value
}

interface QuoteRow {
  item?: string
  pack_size?: number | null
  price_basis?: string | null
  needed_quantity?: number | null
  unit?: string | null
  issues?: string[]
}

function ProcurementTable({ section, items, quotes }: {
  section: (typeof sections)[number]; items: CardResultItem[]; quotes?: QuoteRow[]
}) {
  const { label, heading, icon: Icon, columns } = section
  const differentTerms = section.quote && quotes?.some((quote, index) => quotes.slice(0, index).some(other => (
    other.item === quote.item && (
      (other.pack_size != null && quote.pack_size != null && other.pack_size !== quote.pack_size)
      || (other.price_basis && quote.price_basis && other.price_basis !== quote.price_basis)
    )
  )))
  const displayedFields = new Set(columns.flatMap(column => column.fields))
  return (
    <table className="procurement-table" role="table" aria-label={label}>
      <caption><Icon aria-hidden="true" /> {label}
        {differentTerms && <small className="procurement-compare-note">包装或计价方式不同，请按实买数量和费用比较。</small>}
      </caption>
      <thead role="rowgroup">
        <tr role="row">
          <th scope="col" role="columnheader">{heading}</th>
          {columns.map(column => (
            <th scope="col" role="columnheader" className={column.numeric ? 'procurement-numeric' : undefined}
              key={column.title}>{column.title}</th>
          ))}
        </tr>
      </thead>
      {items.map(item => {
        const fields = new Map(item.metadata?.map(entry => [entry.label, entry]))
        const quote = section.quote ? quotes?.[Number(item.key.slice('quote-'.length))] : undefined
        if (quote?.needed_quantity != null) fields.set('需求数量', {
          label: '需求数量', value: [quote.needed_quantity, quote.unit].filter(v => v != null).join(' '),
        })
        const notes = section.quote ? quote?.issues ?? item.summary?.split('；') ?? [] : []
        const condition = notes.find(note => /规格与需求不同|相同型号与规格|相同的计量单位|有效期已过/.test(note))
        const firstNote = section.quote ? condition ?? notes[0] : item.summary
        const promotion = section.catalog && fields.get('链接')?.value === '推广链接'
        const otherNotes = notes.filter(note => note !== firstNote)
        const evidence = section.quote ? fields.get('原文')?.value : undefined
        const terms = section.quote ? (item.metadata ?? []).filter(entry => (
          !displayedFields.has(entry.label) && entry.label !== '原文' && entry.value !== '资料未注明'
        )) : []
        const hasDetails = evidence || otherNotes.length || terms.some(entry => entry.label !== '来源')
        const detailTitle = evidence ? '报价原文与条款' : '费用与交付条款'
        return (
          <tbody key={item.key} role="rowgroup">
            <tr className="procurement-record" role="row">
              <th scope="row" role="rowheader">
                {item.url
                  ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
                  : item.title}
                {promotion && <small className="procurement-record-note procurement-condition">推广链接</small>}
                {firstNote && (
                  <small className={`procurement-record-note${condition ? ' procurement-condition' : ''}`}>{firstNote}</small>
                )}
              </th>
              {columns.map(column => {
                const entries = column.fields.flatMap(name => fields.get(name) ?? [])
                return (
                  <td key={column.title} role="cell" className={column.numeric ? 'procurement-numeric' : undefined}>
                    <span className="procurement-mobile-label" aria-hidden="true">{column.title}</span>
                    {entries.length ? entries.map(entry => (
                      <span className="procurement-value" key={entry.label}>
                        {column.title !== entry.label && <small>{columnFieldLabels[entry.label] ?? entry.label}</small>}
                        <MetadataValue entry={entry} />
                      </span>
                    )) : <span className="procurement-muted">{section.quote && column.title === '费用' ? '费用待核对' : '—'}</span>}
                  </td>
                )
              })}
            </tr>
            {Boolean(hasDetails) && <tr className="procurement-detail-row" role="row">
              <td colSpan={columns.length + 1} role="cell">
                <details>
                  <summary className="workbench-disclosure" aria-label={`${item.title}的${detailTitle}`}>                     {detailTitle}
                  </summary>
                  <div className="procurement-quote-evidence">
                    {evidence && <blockquote>{evidence}</blockquote>}
                    {terms.length > 0 && <dl>
                      {terms.map(entry => (
                        <div key={entry.label}>
                          <dt>{entry.label}</dt>
                          <dd><MetadataValue entry={entry} /></dd>
                        </div>
                      ))}
                    </dl>}
                    {otherNotes.length > 0 && <div>
                      <strong>待核对项</strong>
                      <ul aria-label="待核对项">{otherNotes.map(note => <li key={note}>{note}</li>)}</ul>
                    </div>}
                  </div>
                </details>
              </td>
            </tr>}
          </tbody>
        )
      })}
    </table>
  )
}

export function ProcurementResult({ data }: { data: CardResultData & { quotes?: QuoteRow[] } }) {
  const tables = sections.map(section => ({
    section, items: data.items.filter(item => section.keyPattern.test(item.key)),
  })).filter(table => table.items.length)
  const tabularItems = new Set(tables.flatMap(table => table.items))
  const remaining = data.items.filter(item => !tabularItems.has(item))
  const title = typeof data.title === 'string' ? data.title : ''
  const description = typeof data.description === 'string' ? data.description : ''
  return (
    <section className="procurement-result" aria-label="采购结果">
      {(title || description) && <header className="procurement-heading">
        {title && <h3>{title}</h3>}
        {description && <p>{description}</p>}
      </header>}
      {Boolean(data.limitations?.length) && (
        <ul className="procurement-limitations" aria-label="采购结果说明">
          {data.limitations?.map(note => <li key={note}>{note}</li>)}
        </ul>
      )}
      {tables.map(table => <ProcurementTable key={table.section.label} {...table} quotes={data.quotes} />)}
      {(remaining.length > 0 || !tables.length) && <CardResult data={{ ...data, items: remaining }} />}
    </section>
  )
}
