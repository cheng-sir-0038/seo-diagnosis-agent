import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TableResult } from '../components/renderers/TableResult'

const data = {
  schema_version: '1.0',
  capability: 'organic_keywords',
  view_type: 'table' as const,
  title: { key: 'seo.organic_keywords.title', fallback: '关键词明细' },
  description: { key: 'seo.organic_keywords.description', fallback: '关键词、排名和对应页面。' },
  provenance: { source: 'dataforseo', source_label: 'DataForSEO', is_estimated: true },
  limitations: [],
  columns: [
    { key: 'keyword', label: { key: 'seo.field.keyword', fallback: '关键词' }, data_type: 'string' as const },
    { key: 'position', label: { key: 'seo.field.position', fallback: '当前排名' }, data_type: 'number' as const, formatter: 'integer' },
    { key: 'position_change', label: { key: 'seo.field.change', fallback: '排名变化' }, data_type: 'number' as const, formatter: 'rank_delta' },
    { key: 'search_volume', label: { key: 'seo.field.volume', fallback: '月均搜索量' }, data_type: 'number' as const, formatter: 'integer' },
    { key: 'url', label: { key: 'seo.field.url', fallback: '排名页面' }, data_type: 'url' as const, formatter: 'compact_url' },
  ],
  rows: Array.from({ length: 9 }, (_, index) => ({
    keyword: index === 0 ? 'baidu china search engine' : `keyword ${index + 1}`,
    position: index + 1,
    position_change: index === 0 ? 5 : 0,
    search_volume: 1000 - index * 10,
    url: `https://example.com/pages/${index + 1}`,
  })),
}

function mockMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

describe('TableResult responsive layout', () => {
  beforeEach(() => mockMatchMedia(false))
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('keeps the sortable table on desktop', () => {
    render(<TableResult data={data} />)

    expect(screen.getByTestId('table-result')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByTestId('mobile-record-list')).not.toBeInTheDocument()
    expect(screen.getByText('左右滑动查看完整指标')).toBeInTheDocument()
  })

  it('renders one complete record card per row on mobile without a table', () => {
    mockMatchMedia(true)
    render(<TableResult data={data} />)

    const list = screen.getByTestId('mobile-record-list')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText('左右滑动查看完整指标')).not.toBeInTheDocument()
    expect(within(list).getAllByTestId('mobile-record-card')).toHaveLength(8)

    const first = within(list).getAllByTestId('mobile-record-card')[0]
    expect(within(first).getByText('baidu china search engine')).toBeInTheDocument()
    expect(within(first).getByText('当前排名')).toBeInTheDocument()
    expect(within(first).getByText('1')).toBeInTheDocument()
    expect(within(first).getByText('上升 5 位')).toBeInTheDocument()
    expect(within(first).getByText('月均搜索量')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: 'example.com/pages/1' })).toHaveAttribute(
      'href',
      'https://example.com/pages/1',
    )
  })

  it('paginates mobile record cards vertically', () => {
    mockMatchMedia(true)
    render(<TableResult data={data} />)

    expect(screen.queryByText('keyword 9')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTitle('2'))
    expect(screen.getByText('keyword 9')).toBeInTheDocument()
    expect(screen.getAllByTestId('mobile-record-card')).toHaveLength(1)
  })

  it('keeps row keys unique when several records share the same URL', () => {
    mockMatchMedia(true)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const sharedUrlData = {
      ...data,
      rows: [
        { keyword: 'baidu', position: 1, position_change: 0, search_volume: 100, url: 'https://www.baidu.com/' },
        { keyword: 'baidu china', position: 2, position_change: 0, search_volume: 90, url: 'https://www.baidu.com/' },
        { keyword: 'baidu china', position: 2, position_change: 0, search_volume: 90, url: 'https://www.baidu.com/' },
      ],
    }

    render(<TableResult data={sharedUrlData} />)

    expect(screen.getAllByTestId('mobile-record-card')).toHaveLength(3)
    const keyWarnings = error.mock.calls
      .map((call) => call.map(String).join(' '))
      .filter((message) => message.includes('same key') || message.includes('unique "key"'))
    expect(keyWarnings).toEqual([])
  })

})
