import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ToolResultView } from '../components/ToolResultView'

const data = {
  schema_version: '1.0', capability: 'news_search', view_type: 'cards', variant: 'news',
  title: { key: 'seo.news.title', fallback: '相关新闻' },
  description: { key: 'seo.news.description', fallback: '查看近期新闻' },
  provenance: { source: 'serper', is_estimated: false }, limitations: [],
  items: [{ key: 'news-1', title: 'SEO Update', url: 'https://news.example/update', image_url: 'https://img.example/update.jpg', summary: 'What changed', metadata: [{ label: '来源', value: 'SEO Daily' }, { label: '发布时间', value: '2 hours ago' }] }],
}

describe('CardResult', () => {
  afterEach(cleanup)

  it('shows an empty query as no returned items instead of a rendering failure', () => {
    render(<ToolResultView hint="cards" data={{ ...data, items: [] }} mode="chat" />)
    expect(screen.getByText('本次查询未返回可展示条目，不代表指标为零。')).toBeInTheDocument()
    expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument()
  })

  it('keeps rejecting malformed items rather than treating them as empty', () => {
    render(<ToolResultView hint="cards" data={{ ...data, items: [{ key: 'invalid' }] }} mode="chat" />)
    expect(screen.queryByTestId('card-result')).not.toBeInTheDocument()
    expect(screen.queryByText('本次查询未返回可展示条目，不代表指标为零。')).not.toBeInTheDocument()
    expect(screen.getByText(/该结果暂时无法以图表展示/)).toBeInTheDocument()
  })

  it('renders reusable card contracts instead of raw JSON', () => {
    render(<ToolResultView hint="cards" data={data} mode="chat" />)
    expect(screen.getByTestId('card-result')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'SEO Update' })).toHaveAttribute('href', 'https://news.example/update')
    expect(screen.getByRole('img', { name: 'SEO Update' })).toHaveAttribute('src', 'https://img.example/update.jpg')
    expect(screen.getByText('What changed')).toBeInTheDocument()
    expect(screen.getByText('SEO Daily')).toBeInTheDocument()
    expect(screen.queryByTestId('raw-tool-result')).not.toBeInTheDocument()
    expect(screen.getByTestId('card-result')).toHaveClass('news')
  })

  it('explains page-review results without turning missing evidence into a success', () => {
    render(<ToolResultView hint="cards" data={{
      ...data, capability: 'page_change_review', variant: undefined,
      items: [
        { key: 'baseline', title: 'baseline_recorded', summary: 'https://example.com/' },
        { key: 'review', title: 'title', summary: 'insufficient_evidence' },
      ],
    }} mode="chat" />)
    expect(screen.getByRole('heading', { name: '页面基线已保存' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '页面标题' })).toBeInTheDocument()
    expect(screen.getByText('证据不足，暂不能判断')).toBeInTheDocument()
    expect(screen.queryByText('与目标一致')).not.toBeInTheDocument()
  })

  it('shows structured suggestions as copyable text without executing HTML', () => {
    const code = '{"name":"<script>alert(1)</script>"}'
    const { container } = render(<ToolResultView hint="cards" data={{
      ...data, capability: 'page_suggestions', variant: 'page-seo-suggestions',
      items: [{ key: 'json_ld', title: 'JSON-LD 建议', code }],
    }} mode="chat" />)
    expect(screen.getByText(code)).toBeInTheDocument()
    expect(container.querySelector('pre code')).toHaveTextContent(code)
    expect(container.querySelector('script')).toBeNull()
  })
})
