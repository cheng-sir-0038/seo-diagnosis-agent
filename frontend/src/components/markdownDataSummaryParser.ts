export interface MarkdownDataSummaryRow {
  label: string
  value: number
  displayValue: string
  note?: string
  percentage: number
  percentageLabel?: string
}

export interface MarkdownDataSummaryData {
  title: string
  rows: MarkdownDataSummaryRow[]
}

export type MarkdownContentSegment =
  | { type: 'markdown'; content: string }
  | { type: 'data-summary'; data: MarkdownDataSummaryData }

const BUSINESS_TERMS = /(排名|top\s*\d|流量|关键词|趋势|分布|覆盖|点击|曝光|转化|外链|页面|占比)/i
const BAR_PATTERN = /[█▉▊▋▌▍▎▏▓▒░▇▆▅▄▃▂▁■□━═─]{2,}/
const TEXT_LANGUAGES = new Set(['', 'text', 'txt', 'plaintext', 'markdown', 'md'])

export function splitMarkdownContent(content: string): MarkdownContentSegment[] {
  const lines = content.split('\n')
  const segments: MarkdownContentSegment[] = []
  let markdown: string[] = []
  let index = 0

  const flushMarkdown = () => {
    if (markdown.length === 0) return
    const value = markdown.join('\n')
    if (value) segments.push({ type: 'markdown', content: value })
    markdown = []
  }

  while (index < lines.length) {
    const opening = lines[index].match(/^\s*(`{3,}|~{3,})([^`]*)$/)
    if (!opening) {
      markdown.push(lines[index])
      index += 1
      continue
    }

    const marker = opening[1]
    const markerCharacter = marker[0]
    const markerLength = marker.length
    const language = opening[2].trim().toLowerCase().split(/\s+/)[0]
    const blockLines: string[] = []
    let cursor = index + 1
    let closed = false

    while (cursor < lines.length) {
      const closing = lines[cursor].match(/^\s*(`{3,}|~{3,})\s*$/)
      if (
        closing
        && closing[1][0] === markerCharacter
        && closing[1].length >= markerLength
      ) {
        closed = true
        break
      }
      blockLines.push(lines[cursor])
      cursor += 1
    }

    const block = blockLines.join('\n')
    const summary = TEXT_LANGUAGES.has(language) ? parseBusinessDataSummary(block) : null
    if (summary) {
      flushMarkdown()
      segments.push({ type: 'data-summary', data: summary })
    } else {
      markdown.push(lines[index], ...blockLines)
      if (closed) markdown.push(lines[cursor])
    }

    index = closed ? cursor + 1 : lines.length
  }

  flushMarkdown()
  return segments.length ? segments : [{ type: 'markdown', content }]
}

export function parseBusinessDataSummary(content: string): MarkdownDataSummaryData | null {
  const lines = content
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  if (lines.length < 2 || !BUSINESS_TERMS.test(content)) return null

  const titleLine = lines.find((line) => (
    /[：:]$/.test(line)
    && BUSINESS_TERMS.test(line)
    && !BAR_PATTERN.test(line)
  ))
  const parsedRows = lines
    .filter((line) => line !== titleLine)
    .map(parseSummaryRow)
    .filter((row): row is Omit<MarkdownDataSummaryRow, 'percentage'> & { unit: string } => row !== null)

  if (parsedRows.length < 2) return null

  const units = new Set(parsedRows.map((row) => row.unit.toLowerCase()))
  const values = parsedRows.map((row) => row.value)
  const total = values.reduce((sum, value) => sum + Math.max(value, 0), 0)
  const maximum = Math.max(...values, 0)
  const title = titleLine?.replace(/[：:]$/, '').trim() || '数据分布摘要'
  const rankingBuckets = parsedRows.every((row) => /(top\s*[\d+]|排名\s*[\d+]|第\s*\d)/i.test(row.label))
  const canUseDistribution = (
    units.size === 1
    && !units.has('%')
    && total > 0
    && (rankingBuckets || /(分布|占比)/.test(title))
  )
  const explicitPercentage = units.size === 1 && units.has('%')

  const rows: MarkdownDataSummaryRow[] = parsedRows.map((row) => {
    let percentage = maximum > 0 ? (Math.max(row.value, 0) / maximum) * 100 : 0
    if (explicitPercentage) percentage = row.value
    if (canUseDistribution) percentage = (Math.max(row.value, 0) / total) * 100
    const normalizedPercentage = clampPercentage(percentage)
    const displayedPercentage = explicitPercentage ? row.value : normalizedPercentage
    return {
      label: row.label,
      value: row.value,
      displayValue: row.displayValue,
      ...(row.note ? { note: row.note } : {}),
      percentage: normalizedPercentage,
      ...((explicitPercentage || canUseDistribution)
        ? { percentageLabel: `${displayedPercentage.toFixed(1)}%` }
        : {}),
    }
  })

  return { title, rows }
}

function parseSummaryRow(
  line: string,
): (Omit<MarkdownDataSummaryRow, 'percentage'> & { unit: string }) | null {
  const bar = line.match(BAR_PATTERN)
  if (!bar || bar.index === undefined) return null

  const label = cleanLabel(line.slice(0, bar.index))
  const tail = line.slice(bar.index + bar[0].length).trim()
  const valueMatch = tail.match(/(-?\d[\d,.]*)(?:\s*)(%|个|词|条|次|k|m)?/i)
  if (!label || !valueMatch || valueMatch.index === undefined) return null

  const rawNumber = valueMatch[1]
  const unit = valueMatch[2] ?? ''
  const value = parseNumericValue(rawNumber, unit)
  if (!Number.isFinite(value)) return null

  const note = tail
    .slice(valueMatch.index + valueMatch[0].length)
    .replace(/^[\s:：→⇒➜\-—]+/, '')
    .trim()

  return {
    label,
    value,
    unit,
    displayValue: `${rawNumber}${unit ? ` ${unit}` : ''}`,
    ...(note ? { note } : {}),
  }
}

function parseNumericValue(raw: string, unit: string): number {
  const value = Number.parseFloat(raw.replaceAll(',', ''))
  if (unit.toLowerCase() === 'k') return value * 1_000
  if (unit.toLowerCase() === 'm') return value * 1_000_000
  return value
}

function cleanLabel(value: string): string {
  return value.replace(/[|｜]+$/g, '').replace(/\s{2,}/g, ' ').trim()
}

function clampPercentage(value: number): number {
  return Math.max(0, Math.min(100, Number(value.toFixed(1))))
}
