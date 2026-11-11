export interface LocalizedText {
  key: string
  fallback: string
}

export interface PresentationProvenance {
  source?: string | null
  source_label?: string | null
  is_estimated: boolean
  integrity?: string | null
  country?: string | null
  fetched_at?: string | null
  strategy?: 'mobile' | 'desktop' | null
  requested_url?: string | null
  final_url?: string | null
  lighthouse_version?: string | null
  categories?: string[]
}

export interface PresentationBase {
  schema_version: string
  capability: string
  view_type: 'chart' | 'table' | 'tree' | 'cards' | 'technical'
  title: LocalizedText
  description: LocalizedText
  provenance: PresentationProvenance
  limitations: string[]
}



export type PageMetadataStatus = 'good' | 'warning' | 'missing' | 'blocked' | 'info'

export interface PageMetadataCheck {
  key: string
  label: string
  value?: string | null
  length: number
  count: number
  status: PageMetadataStatus
  note?: string | null
}

export interface PageMetadataAnalyzer {
  name?: string | null
  version?: string | null
  status: 'disabled' | 'completed' | 'unavailable' | 'failed'
  error?: string | null
  word_count?: number | null
  content_hash?: string | null
  heading_counts: Record<string, number>
  internal_links_count?: number | null
  image_missing_alt_count?: number | null
  generic_anchor_text_count?: number | null
  warnings: string[]
}

export interface PageMetadataData extends PresentationBase {
  view_type: 'chart'
  variant: 'page_metadata'
  http: {
    status_code: number
    content_type?: string | null
    content_length_bytes?: number | null
    final_url?: string | null
  }
  indexing: {
    declared_noindex: boolean
    robots?: string | null
    googlebot?: string | null
    x_robots_tag?: string | null
  }
  checks: PageMetadataCheck[]
  headings: string[]
  additional: {
    charset?: string | null
    og_title?: string | null
    og_description?: string | null
  }
  analyzer?: PageMetadataAnalyzer
  warnings: string[]
}

export interface PagePerformanceScore {
  key: string
  label: string
  value: number | null
  status: 'good' | 'needs_improvement' | 'poor' | 'unknown'
}

export interface PagePerformanceMetric {
  key: string
  label: string
  value: number | null
  display_value: string
  status: 'good' | 'needs_improvement' | 'poor' | 'unknown'
  score?: number | null
}

export interface PagePerformanceIssue {
  audit_id: string
  title: string
  description?: string | null
  priority: 'high' | 'medium' | 'low'
  priority_label?: string
  score?: number | null
  display_value?: string | null
  savings_label?: string | null
}

export interface PagePerformanceData extends PresentationBase {
  view_type: 'chart'
  variant: 'page_performance'
  scores: PagePerformanceScore[]
  metrics: PagePerformanceMetric[]
  opportunities: PagePerformanceIssue[]
  diagnostics: PagePerformanceIssue[]
  warnings: string[]
}

export interface ChartDataset {
  label: string
  data: number[]
}

export interface ChartResultData extends Partial<PresentationBase> {
  type: 'bar' | 'line' | 'pie'
  labels: string[]
  datasets: ChartDataset[]
}

export interface SeoSearchOverviewMetric {
  key: string
  label: string
  value: number
  kind: 'estimate' | 'count'
  helper?: string
}

export interface SeoRankingBucket {
  key: string
  label: string
  value: number
  percentage: number
}

export interface SeoSearchOverviewResultData extends ChartResultData {
  variant: 'seo_search_overview'
  title: LocalizedText
  description: LocalizedText
  provenance: PresentationProvenance
  limitations: string[]
  metrics: SeoSearchOverviewMetric[]
  distribution: SeoRankingBucket[]
  insight: string
  warning?: string | null
  // Backward-compatible alias while existing renderer migrates to provenance.
  meta: PresentationProvenance
}

export type TableDataType = 'string' | 'number' | 'date' | 'boolean' | 'url'
export type ColumnAlignment = 'left' | 'center' | 'right'

export interface PresentationRange {
  min?: number
  max?: number
  label: string
}

export interface TableColumnContract {
  key: string
  label?: LocalizedText
  title?: string
  data_type?: TableDataType
  semantic_type?: string
  formatter?: string
  sortable?: boolean
  align?: ColumnAlignment
  width?: number
  help_text?: string
  null_text?: string
  value_map?: Record<string, string>
  ranges?: PresentationRange[]
}

export interface TableResultData extends Partial<PresentationBase> {
  columns: TableColumnContract[]
  rows: Record<string, unknown>[]
}


export interface CardMetadataItem {
  label: string
  value: string
}

export interface CardResultItem {
  key: string
  title: string
  url?: string | null
  image_url?: string | null
  summary?: string | null
  code?: string
  metadata?: CardMetadataItem[]
}

export interface CardResultData extends Partial<PresentationBase> {
  view_type: 'cards'
  variant?: string
  items: CardResultItem[]
}

export interface TreeNodeContract {
  key: string
  title: string
  children?: TreeNodeContract[]
}

export interface TreeResultData extends Partial<PresentationBase> {
  nodes: TreeNodeContract[]
}

export function resolveLocalizedText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (isLocalizedText(value)) return value.fallback
  return fallback
}

export function unwrapToolResult(data: unknown): unknown {
  if (!isRecord(data)) return data
  if (typeof data.success === 'boolean' && 'data' in data) return data.data
  return data
}

export function isPresentationBase(data: unknown): data is PresentationBase {
  return isRecord(data)
    && typeof data.schema_version === 'string'
    && typeof data.capability === 'string'
    && ['chart', 'table', 'tree', 'cards', 'technical'].includes(String(data.view_type))
    && isLocalizedText(data.title)
    && isLocalizedText(data.description)
    && isProvenance(data.provenance)
    && Array.isArray(data.limitations)
    && data.limitations.every((item) => typeof item === 'string')
}



export function isPageMetadataData(data: unknown): data is PageMetadataData {
  if (!isPresentationBase(data) || data.view_type !== 'chart') return false
  if (!isRecord(data) || data.variant !== 'page_metadata') return false
  if (!isRecord(data.http) || typeof data.http.status_code !== 'number') return false
  if (!isRecord(data.indexing) || typeof data.indexing.declared_noindex !== 'boolean') return false
  if (!Array.isArray(data.checks) || !data.checks.every((item) => (
    isRecord(item)
    && typeof item.key === 'string'
    && typeof item.label === 'string'
    && typeof item.length === 'number'
    && typeof item.count === 'number'
    && ['good', 'warning', 'missing', 'blocked', 'info'].includes(String(item.status))
  ))) return false
  return Array.isArray(data.headings)
    && data.headings.every((item) => typeof item === 'string')
    && Array.isArray(data.warnings)
    && data.warnings.every((item) => typeof item === 'string')
}

export function isPagePerformanceData(data: unknown): data is PagePerformanceData {
  if (!isPresentationBase(data) || data.view_type !== 'chart') return false
  if (!isRecord(data) || data.variant !== 'page_performance') return false
  if (!Array.isArray(data.scores) || !data.scores.every(validPerformanceScore)) return false
  if (!Array.isArray(data.metrics) || !data.metrics.every(validPerformanceMetric)) return false
  if (!Array.isArray(data.opportunities) || !data.opportunities.every(validPerformanceIssue)) return false
  if (!Array.isArray(data.diagnostics) || !data.diagnostics.every(validPerformanceIssue)) return false
  return Array.isArray(data.warnings) && data.warnings.every((item) => typeof item === 'string')
}

function validPerformanceScore(item: unknown): boolean {
  return isRecord(item)
    && typeof item.key === 'string'
    && typeof item.label === 'string'
    && (item.value === null || (typeof item.value === 'number' && Number.isFinite(item.value)))
    && ['good', 'needs_improvement', 'poor', 'unknown'].includes(String(item.status))
}

function validPerformanceMetric(item: unknown): boolean {
  return isRecord(item)
    && typeof item.key === 'string'
    && typeof item.label === 'string'
    && typeof item.display_value === 'string'
    && ['good', 'needs_improvement', 'poor', 'unknown'].includes(String(item.status))
}

function validPerformanceIssue(item: unknown): boolean {
  return isRecord(item)
    && typeof item.audit_id === 'string'
    && typeof item.title === 'string'
    && ['high', 'medium', 'low'].includes(String(item.priority))
}

export function isSeoSearchOverviewResultData(data: unknown): data is SeoSearchOverviewResultData {
  if (!isRecord(data) || data.variant !== 'seo_search_overview' || data.type !== 'bar') return false
  if (!isLocalizedText(data.title) || !isLocalizedText(data.description)) return false
  if (!isProvenance(data.provenance) || !Array.isArray(data.limitations)) return false
  if (typeof data.insight !== 'string') return false
  if (!Array.isArray(data.metrics) || data.metrics.length === 0) return false
  if (!data.metrics.every((metric) => (
    isRecord(metric)
    && typeof metric.key === 'string'
    && typeof metric.label === 'string'
    && typeof metric.value === 'number'
    && Number.isFinite(metric.value)
    && ['estimate', 'count'].includes(String(metric.kind))
    && (metric.helper === undefined || typeof metric.helper === 'string')
  ))) return false
  if (!Array.isArray(data.distribution)) return false
  if (!data.distribution.every((bucket) => (
    isRecord(bucket)
    && typeof bucket.key === 'string'
    && typeof bucket.label === 'string'
    && typeof bucket.value === 'number'
    && Number.isFinite(bucket.value)
    && typeof bucket.percentage === 'number'
    && Number.isFinite(bucket.percentage)
  ))) return false
  return isProvenance(data.meta)
}

export function isChartResultData(data: unknown): data is ChartResultData {
  if (!isRecord(data) || !['bar', 'line', 'pie'].includes(String(data.type))) return false
  if (
    !Array.isArray(data.labels)
    || data.labels.length === 0
    || !data.labels.every((item) => typeof item === 'string')
  ) return false
  if (!Array.isArray(data.datasets) || data.datasets.length === 0) return false
  const labels = data.labels as string[]
  const labelsUnique = new Set<string>()
  return data.datasets.every((item) => {
    if (!isRecord(item) || typeof item.label !== 'string' || labelsUnique.has(item.label)) return false
    labelsUnique.add(item.label)
    return Array.isArray(item.data)
      && item.data.length === labels.length
      && item.data.every((value) => typeof value === 'number' && Number.isFinite(value))
  })
}

export function isTableResultData(data: unknown): data is TableResultData {
  if (!isRecord(data) || !Array.isArray(data.columns) || !Array.isArray(data.rows)) return false
  if (data.columns.length === 0) return false
  const keys = new Set<string>()
  const validColumns = data.columns.every((column) => {
    if (!isRecord(column) || typeof column.key !== 'string' || !column.key) return false
    if (!(typeof column.title === 'string' || isLocalizedText(column.label))) return false
    if (keys.has(column.key)) return false
    keys.add(column.key)
    if (
      column.data_type !== undefined
      && !['string', 'number', 'date', 'boolean', 'url'].includes(String(column.data_type))
    ) return false
    if (column.align !== undefined && !['left', 'center', 'right'].includes(String(column.align))) return false
    if (column.value_map !== undefined && !isStringMap(column.value_map)) return false
    return column.ranges === undefined
      || (Array.isArray(column.ranges) && column.ranges.every(isPresentationRange))
  })
  return validColumns && data.rows.every(isRecord)
}


export function isCardResultData(data: unknown): data is CardResultData {
  if (!isRecord(data) || data.view_type !== 'cards' || !Array.isArray(data.items)) return false
  return data.items.length > 0 && data.items.every((item) => (
    isRecord(item)
    && typeof item.key === 'string'
    && typeof item.title === 'string'
    && (item.url === undefined || item.url === null || typeof item.url === 'string')
    && (item.image_url === undefined || item.image_url === null || typeof item.image_url === 'string')
    && (item.summary === undefined || item.summary === null || typeof item.summary === 'string')
    && (item.code === undefined || typeof item.code === 'string')
    && (item.metadata === undefined || (
      Array.isArray(item.metadata)
      && item.metadata.every((entry) => isRecord(entry) && typeof entry.label === 'string' && typeof entry.value === 'string')
    ))
  ))
}

export function isTreeResultData(data: unknown): data is TreeResultData {
  if (!isRecord(data) || !Array.isArray(data.nodes) || data.nodes.length === 0) return false
  const keys = new Set<string>()
  return data.nodes.every((node) => validNode(node, keys))
}

function isPresentationRange(value: unknown): value is PresentationRange {
  return isRecord(value)
    && typeof value.label === 'string'
    && (value.min === undefined || typeof value.min === 'number')
    && (value.max === undefined || typeof value.max === 'number')
}

function isLocalizedText(value: unknown): value is LocalizedText {
  return isRecord(value) && typeof value.key === 'string' && typeof value.fallback === 'string'
}

function isProvenance(value: unknown): value is PresentationProvenance {
  return isRecord(value) && typeof value.is_estimated === 'boolean'
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === 'string')
}

function validNode(value: unknown, keys: Set<string>): value is TreeNodeContract {
  if (
    !isRecord(value)
    || typeof value.key !== 'string'
    || !value.key
    || typeof value.title !== 'string'
  ) return false
  if (keys.has(value.key)) return false
  keys.add(value.key)
  return value.children === undefined
    || (Array.isArray(value.children) && value.children.every((child) => validNode(child, keys)))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
