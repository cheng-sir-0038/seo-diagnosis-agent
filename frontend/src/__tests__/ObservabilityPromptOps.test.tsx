import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'

const json = (data: unknown) =>
  Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))

function mockProductFetch() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/sites/site-1')) return json({ id: 'site-1', name: 'Demo', domain: 'example.com' })
    if (url.includes('/workbench/summary')) return json({ pending_approvals: 0, high_value_opportunities: 0, medium_opportunities: 0, active_tasks: 0, active_scan_runs: 0, active_monitoring_plans: 0, recent_work_records: [] })
    if (url.includes('/observability/traces')) return json([])
    if (url.includes('/observability/issues')) return json([])
    if (url.includes('/observability/metrics')) return json([])
    if (url.includes('/promptops/prompts')) return json([])
    if (url.includes('/promptops/datasets')) return json([])
    if (url.includes('/promptops/evaluations')) return json([])
    if (url.includes('/promptops/experiments')) return json([])
    return json([])
  })
}

describe('Observability and PromptOps product pages', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.history.replaceState(null, '', '/')
    window.localStorage.setItem(
      'seo-agent-product-config-v1',
      JSON.stringify({ siteId: 'site-1', ownerActorId: 'owner-1', agentActorId: 'agent-1', executorActorId: 'executor-1' }),
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('loads the observability page from real APIs', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText('向 SEO 优化专员提问')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /运行观测/ }))
    expect(await screen.findByRole('heading', { name: '运行观测' })).toBeInTheDocument()
    expect(screen.getByText('暂无 Trace')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces'), expect.anything()))
  })

  it('loads the PromptOps page and shows empty real state', async () => {
    vi.stubGlobal('fetch', mockProductFetch())
    render(<App />)
    expect(await screen.findByText('向 SEO 优化专员提问')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /提示词实验/ }))
    expect(await screen.findByRole('heading', { name: '提示词实验' })).toBeInTheDocument()
    expect(screen.getByText('暂无 Prompt')).toBeInTheDocument()
  })
})
