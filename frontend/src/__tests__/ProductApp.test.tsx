import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { loadProductConfig, saveProductConfig } from '../services/productApi'

const json = (data: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }))

describe('business chat workspace', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    window.history.replaceState(null, '', '/')
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens chat in a fresh browser and makes no site or actor requests', async () => {
    const fetchMock = vi.fn(() => json([]))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    expect(screen.getByRole('button', { name: /页面检查/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /医保药品目录/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /PubMed 文献检索/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /医考报名/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'SEO 站点登记' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /考研招生对比/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /网页与考纲联查/ }))
    expect((screen.getByLabelText('给业务助理发送消息') as HTMLTextAreaElement).value).toContain('www.hbwsrc.cn/')
    expect(screen.queryByLabelText('站点域名')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('loads registered tools on demand and retries a failed directory request', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => json({}, 503))
      .mockImplementationOnce(() => json({ specialists: [
        {
          agent: 'healthcare', tools: [{ name: 'medical_exam_syllabus', description: '核对官方考纲' }],
          skills: [{ name: 'medical-exam-syllabus', label: '医考考纲核对', tools: ['medical_exam_syllabus'] }],
        },
        { agent: 'seo', tools: [] },
      ] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '专员与工具' }))
    fireEvent.click(await screen.findByRole('button', { name: '重试' }))
    expect(await screen.findByText('查证考纲条款')).toBeVisible()
    expect(screen.getByText('Skill · 医考考纲核对')).toBeVisible()
    expect(screen.getByLabelText('1 个工具')).toBeVisible()
    expect(fetchMock.mock.calls.every(call => String(call[0]).includes('/api/v1/chat/tools?conversation_id='))).toBe(true)
  })

  it('validates registration from its direct URL and can return to chat', async () => {
    const fetchMock = vi.fn(() => json([]))
    vi.stubGlobal('fetch', fetchMock)
    window.history.replaceState(null, '', '/#/site-setup')
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: '登记站点' }))

    expect(await screen.findByText('请先完成所有必填项')).toBeInTheDocument()
    expect(screen.getByLabelText('站点域名')).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('link', { name: '返回对话' }))
    await waitFor(() => expect(window.location.hash).toBe('#/chat'))
    expect(await screen.findByLabelText('给业务助理发送消息')).toBeEnabled()
  })

  it('returns to chat after explicitly registering an SEO site', async () => {
    window.history.replaceState(null, '', '/#/site-setup')
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
    fireEvent.click(screen.getByRole('button', { name: '登记站点' }))

    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    expect(createdSitePayload).toMatchObject({ default_country: 'US', default_language: 'en-US' })
  })

  it('clears only the previous workspace data and preserves data from the new version', async () => {
    const productConfig = { siteId: 'site-1', ownerActorId: 'owner-1', agentActorId: 'agent-1', executorActorId: 'executor-1' }
    localStorage.setItem('seo-agent-product-config-v1', JSON.stringify(productConfig))
    localStorage.setItem('agent-workspace-conversations-v1', JSON.stringify([
      { id: 'old', title: '旧站点对话', messages: [], updatedAt: '2026-07-24' },
    ]))
    sessionStorage.setItem('agent-workspace-active-conversation-v1', 'old')
    localStorage.setItem('unrelated', 'local data')
    sessionStorage.setItem('unrelated', 'tab data')
    window.history.replaceState(null, '', '/#/schedules')
    const fetchMock = vi.fn(() => json({ detail: '站点不存在' }, 404))
    vi.stubGlobal('fetch', fetchMock)
    const first = render(<App />)
    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    expect(screen.queryByText('旧站点对话')).not.toBeInTheDocument()
    expect(localStorage.getItem('seo-agent-product-config-v1')).toBeNull()
    expect(localStorage.getItem('agent-workspace-conversations-v1')).toBeNull()
    expect(sessionStorage.getItem('agent-workspace-active-conversation-v1')).toBeNull()
    expect(localStorage.getItem('unrelated')).toBe('local data')
    expect(sessionStorage.getItem('unrelated')).toBe('tab data')
    const active = sessionStorage.getItem('agent-workspace-active-conversation-v2')
    expect(active).toBeTruthy()
    saveProductConfig(productConfig)

    first.unmount()
    render(<App />)
    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    expect(sessionStorage.getItem('agent-workspace-active-conversation-v2')).toBe(active)
    expect(loadProductConfig()).toEqual(productConfig)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
