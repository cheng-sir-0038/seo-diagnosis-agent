import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ToolResultView } from '../components/ToolResultView'


describe('ToolResultView', () => {
  const OriginalResizeObserver = globalThis.ResizeObserver

  beforeEach(() => {
    class TestResizeObserver implements ResizeObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{
          target,
          contentRect: { width: 320, height: 230, x: 0, y: 0, top: 0, left: 0, right: 320, bottom: 230, toJSON: () => ({}) },
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
  it('renders a sortable table from a tool result envelope', () => {
    render(
      <ToolResultView
        hint="table"
        data={{
          success: true,
          data: {
            columns: [
              { key: 'keyword', title: '关键词', data_type: 'string', sortable: true },
              { key: 'traffic', title: '流量', data_type: 'number', sortable: true },
            ],
            rows: [
              { keyword: 'seo agent', traffic: 320 },
              { keyword: 'seo automation', traffic: 210 },
            ],
          },
        }}
      />,
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('seo agent')).toBeInTheDocument()
  })

  it('renders tree data when keys are unique', () => {
    render(
      <ToolResultView
        hint="tree"
        data={{ nodes: [{ key: 'critical', title: '严重问题', children: [{ key: 'robots', title: 'robots 阻塞' }] }] }}
      />,
    )

    expect(screen.getByText('严重问题')).toBeInTheDocument()
  })

  it('renders a valid chart contract', async () => {
    render(
      <ToolResultView
        hint="chart"
        data={{
          type: 'line',
          labels: ['Jan', 'Feb'],
          datasets: [{ label: '自然流量', data: [1000, 800] }],
        }}
      />,
    )

    expect(await screen.findByTestId('chart-result')).toBeInTheDocument()
  })

  it('renders a plain-language SEO search overview instead of a generic mixed-scale chart', async () => {
    render(
      <ToolResultView
        hint="chart"
        mode="chat"
        data={{
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
        }}
      />,
    )

    expect(await screen.findByTestId('seo-search-overview')).toBeInTheDocument()
    expect(screen.getByText('预估自然流量')).toBeInTheDocument()
    expect(screen.getByText('52,000')).toBeInTheDocument()
    expect(screen.getByText('79.9%')).toBeInTheDocument()
    expect(screen.getByText(/数据来源：DataForSEO/)).toBeInTheDocument()
    expect(screen.queryByText('流量（×1,000）')).not.toBeInTheDocument()
  })

  it('falls back when tree keys are duplicated', () => {
    render(
      <ToolResultView
        hint="tree"
        data={{ nodes: [{ key: 'same', title: 'A' }, { key: 'same', title: 'B' }] }}
      />,
    )

    expect(screen.getByTestId('raw-tool-result')).toHaveTextContent('same')
  })

  it('falls back to raw JSON when hinted data is invalid', () => {
    render(<ToolResultView hint="chart" data={{ type: 'line', labels: ['Jan'], datasets: [] }} />)

    expect(screen.getByTestId('raw-tool-result')).toHaveTextContent('datasets')
  })

  it('falls back to raw JSON when the hint is absent', () => {
    render(<ToolResultView data={{ any: 'value' }} />)

    expect(screen.getByTestId('raw-tool-result')).toHaveTextContent('value')
  })

  it('renders capability contract labels and semantic cell formatters without provider logic', () => {
    render(
      <ToolResultView
        hint="table"
        mode="chat"
        data={{
          schema_version: '1.0',
          capability: 'organic_keywords',
          view_type: 'table',
          title: { key: 'seo.organic_keywords.title', fallback: '关键词明细' },
          description: { key: 'seo.organic_keywords.description', fallback: '查看具体关键词' },
          provenance: { source: 'dataforseo', is_estimated: true, integrity: 'complete' },
          limitations: ['关键词难度分级为产品内部展示口径'],
          columns: [
            {
              key: 'position_change',
              label: { key: 'seo.field.position_change', fallback: '排名变化' },
              data_type: 'number',
              semantic_type: 'rank_delta',
              formatter: 'rank_delta',
              sortable: true,
              null_text: '暂无历史数据',
            },
            {
              key: 'keyword_difficulty',
              label: { key: 'seo.field.keyword_difficulty', fallback: '关键词难度' },
              data_type: 'number',
              semantic_type: 'difficulty',
              formatter: 'difficulty',
              ranges: [
                { max: 29, label: '较容易' },
                { min: 30, max: 49, label: '中等' },
                { min: 50, max: 69, label: '较难' },
                { min: 70, label: '非常难' },
              ],
            },
            {
              key: 'intent',
              label: { key: 'seo.field.intent', fallback: '搜索意图' },
              data_type: 'string',
              semantic_type: 'search_intent',
              formatter: 'enum',
              value_map: { commercial: '商业调研' },
            },
            {
              key: 'url',
              label: { key: 'seo.field.url', fallback: '排名页面' },
              data_type: 'url',
              semantic_type: 'url',
              formatter: 'compact_url',
            },
          ],
          rows: [
            { position_change: 0, keyword_difficulty: 42, intent: 'commercial', url: 'https://example.com/very/long/path' },
            { position_change: 5, keyword_difficulty: 75, intent: 'commercial', url: 'https://example.com/up' },
            { position_change: -3, keyword_difficulty: 22, intent: 'commercial', url: 'https://example.com/down' },
            { position_change: null, keyword_difficulty: null, intent: null, url: null },
          ],
        }}
      />,
    )

    expect(screen.getByRole('columnheader', { name: /排名变化/ })).toBeInTheDocument()
    expect(screen.getByText('持平')).toBeInTheDocument()
    expect(screen.getByText('上升 5 位')).toBeInTheDocument()
    expect(screen.getByText('下降 3 位')).toBeInTheDocument()
    expect(screen.getAllByText('暂无历史数据').length).toBeGreaterThan(0)
    expect(screen.getByText('42 · 中等')).toBeInTheDocument()
    expect(screen.getByText('75 · 非常难')).toBeInTheDocument()
    expect(screen.getAllByText('商业调研')).toHaveLength(3)
    expect(screen.getByRole('link', { name: 'example.com/very/long/path' })).toHaveAttribute(
      'href',
      'https://example.com/very/long/path',
    )
  })


  it('renders a page performance chart contract for non-technical users', async () => {
    render(
      <ToolResultView
        hint="chart"
        mode="chat"
        data={{
          schema_version: '1.0',
          capability: 'page_performance',
          view_type: 'chart',
          variant: 'page_performance',
          title: { key: 'seo.page_performance.title', fallback: '页面速度诊断' },
          description: {
            key: 'seo.page_performance.description',
            fallback: '查看当前页面的 Lighthouse 实验室检测结果。',
          },
          provenance: {
            source: 'pagespeed',
            source_label: 'Google PageSpeed Insights',
            is_estimated: true,
            fetched_at: '2026-07-25T10:00:00Z',
            strategy: 'mobile',
            requested_url: 'https://example.com/',
            final_url: 'https://example.com/',
            lighthouse_version: '12.8.2',
          },
          limitations: [
            '这是一次 Lighthouse 实验室模拟检测，不等同于真实用户长期体验。',
            '真实用户 Core Web Vitals 需要后续结合独立 CrUX 数据判断。',
          ],
          scores: [
            { key: 'performance', label: '性能', value: 62, status: 'needs_improvement' },
            { key: 'accessibility', label: '无障碍', value: 84, status: 'needs_improvement' },
            { key: 'best-practices', label: '最佳实践', value: 78, status: 'needs_improvement' },
            { key: 'seo', label: 'SEO', value: 91, status: 'good' },
          ],
          metrics: [
            { key: 'lcp', label: '最大内容绘制', value: 3600, display_value: '3.6 秒', status: 'needs_improvement' },
            { key: 'cls', label: '布局偏移', value: 0.08, display_value: '0.08', status: 'good' },
          ],
          opportunities: [
            {
              audit_id: 'unused-javascript',
              title: '减少未使用的 JavaScript',
              description: '移除当前页面没有执行的脚本。',
              priority: 'high',
              priority_label: '优先处理',
              savings_label: '预计节省 1.8 秒 · 410 KB',
            },
          ],
          diagnostics: [],
          warnings: [],
        }}
      />,
    )

    expect(await screen.findByTestId('page-performance-result')).toBeInTheDocument()
    expect(screen.getByText('性能')).toBeInTheDocument()
    expect(screen.getByText('62')).toBeInTheDocument()
    expect(screen.getAllByText('需要改进').length).toBeGreaterThan(0)
    expect(screen.getByText('最大内容绘制')).toBeInTheDocument()
    expect(screen.getByText('3.6 秒')).toBeInTheDocument()
    expect(screen.getByText('减少未使用的 JavaScript')).toBeInTheDocument()
    expect(screen.getByText('预计节省 1.8 秒 · 410 KB')).toBeInTheDocument()
    expect(screen.getByText(/CrUX/)).toBeInTheDocument()
  })

})


it('renders page metadata as a non-technical HTML evidence report', async () => {
  render(
    <ToolResultView
      hint="chart"
      mode="chat"
      data={{
        schema_version: '1.0',
        capability: 'page_metadata',
        view_type: 'chart',
        variant: 'page_metadata',
        title: { key: 'seo.page_metadata.title', fallback: '页面元信息检查' },
        description: { key: 'seo.page_metadata.description', fallback: '直接读取页面源 HTML' },
        provenance: { source: 'page_html', source_label: '页面源 HTML', is_estimated: false, final_url: 'https://example.com/' },
        limitations: ['不执行页面 JavaScript'],
        http: { status_code: 200, content_type: 'text/html', final_url: 'https://example.com/' },
        indexing: { declared_noindex: false, robots: 'index,follow' },
        checks: [
          { key: 'title', label: 'Title', value: 'Example', length: 7, count: 1, status: 'good' },
          { key: 'keywords', label: 'Meta Keywords', value: 'seo', length: 3, count: 1, status: 'info', note: 'Google 不使用该标签进行索引或排名。' },
        ],
        headings: ['Main heading'],
        additional: { charset: 'utf-8' },
        analyzer: {
          name: 'python-seo-analyzer',
          version: '2025.4.3',
          status: 'completed',
          word_count: 88,
          heading_counts: { h1: 1, h2: 2 },
          internal_links_count: 6,
          image_missing_alt_count: 1,
          generic_anchor_text_count: 0,
          warnings: ['Image missing alt tag: /hero.jpg'],
        },
        warnings: [],
      }}
    />,
  )

  expect(await screen.findByTestId('page-metadata-result')).toBeInTheDocument()
  expect(screen.getByText('Example')).toBeInTheDocument()
  expect(screen.getByText(/Google 不使用该标签/)).toBeInTheDocument()
  expect(screen.getByText('H1 1：Main heading')).toBeInTheDocument()
  expect(screen.getByText('基础 On-page 扫描已完成')).toBeInTheDocument()
  expect(screen.getByText('88')).toBeInTheDocument()
  expect(screen.getByText('图片缺少 alt')).toBeInTheDocument()
  expect(screen.getByText(/第三方工具原始观察/)).toBeInTheDocument()
})
