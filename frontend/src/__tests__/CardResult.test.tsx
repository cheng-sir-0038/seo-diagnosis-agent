import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ToolResultView } from '../components/ToolResultView'

const data = {
  schema_version: '1.0', capability: 'news_search', view_type: 'cards', variant: 'news',
  title: { key: 'seo.news.title', fallback: '相关新闻' },
  description: { key: 'seo.news.description', fallback: '查看近期新闻' },
  provenance: { source: 'serper', is_estimated: false }, limitations: [],
  items: [{ key: 'news-1', title: 'SEO Update', url: 'https://news.example/update', image_url: 'https://img.example/update.jpg', summary: 'What changed', metadata: [{ label: '来源', value: 'SEO Daily' }, { label: '发布时间', value: '2 hours ago' }] }],
}

describe('CardResult', () => {
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
})
