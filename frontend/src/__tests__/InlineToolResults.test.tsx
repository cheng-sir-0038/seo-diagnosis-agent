import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { InlineToolResults } from '../components/InlineToolResults'
import type { ToolRun } from '../types/agent'

const tablePresentation = (rows: Record<string, unknown>[], fetchedAt: string) => ({
  success: true,
  data: {
    schema_version: '1.0',
    capability: 'organic_competitors',
    view_type: 'table',
    title: { key: 'seo.organic_competitors.title', fallback: '自然搜索竞争对手' },
    description: { key: 'seo.organic_competitors.description', fallback: '查看竞争网站及共同关键词。' },
    provenance: {
      source: 'dataforseo',
      source_label: 'DataForSEO',
      is_estimated: true,
      integrity: 'complete',
      fetched_at: fetchedAt,
    },
    limitations: [],
    columns: [
      { key: 'domain', title: '竞争网站', data_type: 'string' },
      { key: 'common_keywords', title: '共同关键词', data_type: 'number' },
    ],
    rows,
  },
})

const searchOverview = {
  success: true,
  data: {
    schema_version: '1.0',
    capability: 'search_metrics',
    view_type: 'chart',
    type: 'bar',
    variant: 'seo_search_overview',
    title: { key: 'seo.search_metrics.title', fallback: '自然搜索概览' },
    description: { key: 'seo.search_metrics.description', fallback: '查看当前流量、关键词覆盖和排名分布。' },
    provenance: { source: 'dataforseo', source_label: 'DataForSEO', is_estimated: true, country: 'US', integrity: 'complete' },
    limitations: ['自然流量为估算数据'],
    labels: ['排名 1-3', '排名 4-10', '排名 11+'],
    datasets: [{ label: '关键词数量', data: [43, 0, 171] }],
    metrics: [
      { key: 'organic_traffic', label: '预估自然流量', value: 52000, kind: 'estimate' },
      { key: 'organic_keywords', label: '有排名的关键词', value: 214, kind: 'count' },
      { key: 'top_3', label: 'Top 3 关键词', value: 43, kind: 'count' },
      { key: 'top_10', label: 'Top 10 关键词', value: 43, kind: 'count' },
    ],
    distribution: [
      { key: 'top_3', label: '排名 1-3', value: 43, percentage: 20.1 },
      { key: 'top_4_10', label: '排名 4-10', value: 0, percentage: 0 },
      { key: 'outside_top_10', label: '排名 11+', value: 171, percentage: 79.9 },
    ],
    insight: '43 个关键词已进入前 3 名，171 个仍在前 10 名之外。',
    meta: { source: 'dataforseo', is_estimated: true, country: 'US', integrity: 'complete' },
  },
}

describe('InlineToolResults', () => {
  const OriginalResizeObserver = globalThis.ResizeObserver

  beforeEach(() => {
    class TestResizeObserver implements ResizeObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{
          target,
          contentRect: { width: 720, height: 360, x: 0, y: 0, top: 0, left: 0, right: 720, bottom: 360, toJSON: () => ({}) },
          borderBoxSize: [],
          contentBoxSize: [],
          devicePixelContentBoxSize: [],
        }], this)
      }
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: TestResizeObserver })
  })

  afterEach(() => {
    cleanup()
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: OriginalResizeObserver })
  })

  it('shows identical business evidence once even when tool ids and fetched times differ', () => {
    const rows = [{ domain: 'competitor.example', common_keywords: 126 }]
    const tools: ToolRun[] = [
      {
        id: 'call-1',
        name: 'seo_organic_competitors',
        status: 'completed',
        render_hint: 'table',
        latencyMs: 120,
        result: tablePresentation(rows, '2026-07-28T06:00:00Z'),
      },
      {
        id: 'call-2',
        name: 'seo_organic_competitors',
        status: 'completed',
        render_hint: 'table',
        latencyMs: 240,
        result: tablePresentation(rows, '2026-07-28T06:05:00Z'),
      },
    ]

    render(<InlineToolResults tools={tools} />)

    expect(screen.getAllByRole('table')).toHaveLength(1)
    expect(screen.getAllByText('自然搜索竞争对手')).toHaveLength(1)
    expect(screen.getAllByText('competitor.example')).toHaveLength(1)
  })

  it('keeps separate results when the business rows are different', () => {
    const tools: ToolRun[] = [
      {
        id: 'call-1',
        name: 'seo_organic_competitors',
        status: 'completed',
        render_hint: 'table',
        result: tablePresentation([{ domain: 'first.example', common_keywords: 126 }], '2026-07-28T06:00:00Z'),
      },
      {
        id: 'call-2',
        name: 'seo_organic_competitors',
        status: 'completed',
        render_hint: 'table',
        result: tablePresentation([{ domain: 'second.example', common_keywords: 93 }], '2026-07-28T06:05:00Z'),
      },
    ]

    render(<InlineToolResults tools={tools} />)

    expect(screen.getAllByRole('table')).toHaveLength(2)
    expect(screen.getByText('first.example')).toBeInTheDocument()
    expect(screen.getByText('second.example')).toBeInTheDocument()
  })

  it('does not repeat the title of a self-contained search overview', async () => {
    const tools: ToolRun[] = [{
      id: 'call-overview',
      name: 'seo_search_metrics',
      status: 'completed',
      render_hint: 'chart',
      result: searchOverview,
    }]

    render(<InlineToolResults tools={tools} />)

    const overview = await screen.findByTestId('seo-search-overview')
    expect(screen.getAllByText('自然搜索概览')).toHaveLength(1)
    expect(overview.closest('article')).toHaveClass('self-contained')
  })
})
