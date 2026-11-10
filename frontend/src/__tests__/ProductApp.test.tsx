import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { saveProductConfig } from '../services/productApi'

const json = (data: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }))

describe('SEO specialist product app', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.history.replaceState(null, '', '/')
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the setup screen when no site exists', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json([])))
    render(<App />)
    expect(await screen.findByText('配置你的 SEO 优化专员')).toBeInTheDocument()
    expect(screen.getByLabelText('站点域名')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '目标国家' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '默认语言' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /创建并进入对话工作区/ })).toBeInTheDocument()
  })

  it('explains why no request is sent when required setup fields are missing', async () => {
    const fetchMock = vi.fn(() => json([]))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /创建并进入对话工作区/ }))

    expect(await screen.findByText('请先完成所有必填项')).toBeInTheDocument()
    expect(screen.getByLabelText('站点域名')).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('creates the workspace and enters the chat interface', async () => {
    const site = {
      id: 'site-new',
      name: 'SEO 演示站点',
      domain: 'example.com',
      default_country: 'US',
      default_language: 'en-US',
      timezone: 'Asia/Shanghai',
    }
    let createdSitePayload: Record<string, unknown> | undefined
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/v1/seo-agent/system/sites') && method === 'POST') {
        createdSitePayload = JSON.parse(String(init?.body)) as Record<string, unknown>
        return json(site)
      }
      if (url.includes('/api/v1/seo-agent/system/actors') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as { actor_type: string; name: string; email?: string }
        return json({ id: `actor-${body.actor_type.toLowerCase()}`, ...body })
      }
      if (url.includes('/api/v1/seo-agent/notifications/subscriptions') && method === 'POST') return json({ id: 'subscription-1' })
      if (url.endsWith('/api/v1/seo-agent/system/sites/site-new')) return json(site)
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.change(await screen.findByLabelText('站点域名'), { target: { value: 'example.com' } })
    fireEvent.change(screen.getByLabelText('负责人邮箱'), { target: { value: 'owner@example.com' } })
    fireEvent.change(screen.getByLabelText('执行者邮箱'), { target: { value: 'writer@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: /创建并进入对话工作区/ }))

    expect(await screen.findByText('向 SEO 优化专员提问')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /对话/ })).toHaveAttribute('aria-selected', 'true')
    expect(createdSitePayload).toMatchObject({ default_country: 'US', default_language: 'en-US' })
  })

  it('opens the GPT-style chat first and switches product tabs', async () => {
    const productConfig = { siteId: 'site-1', ownerActorId: 'owner-1', agentActorId: 'agent-1', executorActorId: 'executor-1' }
    saveProductConfig(productConfig)
    window.localStorage.setItem('seo-agent-product-config-v1', JSON.stringify(productConfig))
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/sites/site-1')) return json({ id: 'site-1', name: 'Demo', domain: 'example.com' })
      if (url.includes('/workbench/summary')) return json({ pending_approvals: 1, high_value_opportunities: 2, medium_opportunities: 3, active_tasks: 4, active_scan_runs: 0, active_monitoring_plans: 1, recent_work_records: [] })
      if (url.includes('/approvals')) return json([])
      if (url.includes('/opportunities')) return json([])
      if (url.includes('/schedules')) return json([])
      if (url.includes('/tasks')) return json([])
      if (url.includes('/subscriptions')) return json([])
      if (url.includes('/system/status')) return json({ database_started: true, checkpoint_started: true, adapters: [] })
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText('向 SEO 优化专员提问')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /对话/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /数据总览/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /工作计划/ }))
    expect(await screen.findByRole('heading', { name: '工作计划' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  })
})
