import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { createAgentRun } from '../services/agentApi'

const file = {
  id: 'f'.repeat(32), name: '验收资料.md', size_bytes: 30, text_chars: 16,
  uploaded_at: '2026-11-24T04:00:00Z', in_library: false,
}
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const events = [
  [1, 'run.started', { status: 'running' }],
  [2, 'message.completed', { content: '附件已读取。' }],
  [3, 'run.completed', { status: 'completed' }],
].map(([sequence, type, payload]) => `id: ${sequence}\nevent: ${type}\ndata: ${JSON.stringify({
  sequence, type, payload, run_id: 'doc-run', event_id: String(sequence), created_at: file.uploaded_at,
})}\n\n`).join('')

describe('workspace resource features', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    window.history.replaceState(null, '', '/')
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows the library illustration on an empty library and uses a separate search empty state', async () => {
    window.history.replaceState(null, '', '/#/knowledge')
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json({ documents: [], references: [] }))))
    render(<App />)

    const library = await screen.findByRole('region', { name: '我的资料' })
    expect(await within(library).findByText('这里还没有上传资料。')).toBeVisible()
    expect(library.querySelector('img')).toHaveAttribute('src', expect.stringContaining('workbench-knowledge-empty.webp'))

    fireEvent.change(within(library).getByRole('textbox', { name: '搜索知识库' }), { target: { value: '报价' } })
    fireEvent.click(within(library).getByRole('button', { name: '搜索' }))
    expect(await within(library).findByText('没有匹配的上传资料。')).toBeVisible()
    expect(library.querySelector('img')).toBeNull()
  })

  it('uploads without creating a run, retains the draft after rejection, and sends only IDs', async () => {
    let createAttempts = 0
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workspace/documents?') && options?.method === 'POST') return json(file, 201)
      if (url.endsWith('/chat/runs')) {
        if (createAttempts++ === 0) return json({ detail: '选中资料暂不可用。' }, 503)
        return json({ run_id: 'doc-run', status: 'pending', events_url: '/events', cancel_url: '/cancel' }, 201)
      }
      if (url.endsWith('/events')) return new Response(events)
      throw new Error(url)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<App />)
    const picker = container.querySelector('input[type=file]')!
    fireEvent.change(picker, { target: { files: [new File(['only on upload'], file.name)] } })
    const chips = within(await screen.findByRole('list', { name: '待发送附件' }))
    expect(chips.getByText(file.name)).toBeVisible()
    expect(createAttempts).toBe(0)
    fireEvent.change(screen.getByRole('textbox', { name: '给业务助理发送消息' }), {
      target: { value: '请核对附件内容' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('选中资料暂不可用。')
    expect(screen.getByRole('textbox', { name: '给业务助理发送消息' })).toHaveValue('请核对附件内容')
    expect(screen.getByRole('list', { name: '待发送附件' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('附件已读取。')).toBeVisible()
    expect(screen.getByRole('list', { name: '本条消息的附件' })).toBeVisible()
    const submit = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/chat/runs')).at(-1)?.[1]
    expect(JSON.parse(String(submit?.body))).toMatchObject({ document_ids: [file.id], messages: [{ role: 'user', content: '请核对附件内容' }] })
    expect(String(submit?.body)).not.toContain('only on upload')
    expect(new Headers(submit?.headers).get('X-Workspace-Key')).toBeTruthy()
  })

  it('searches the knowledge library and brings a hit into a new draft', async () => {
    window.history.replaceState(null, '', '/#/knowledge')
    const fetchMock = vi.fn((input: RequestInfo | URL) => json({
      documents: String(input).includes('q=%E8') ? [{ ...file, in_library: true, excerpt: { location: '片段 1', text: '识别号是 Q17' } }] : [],
      references: [],
    }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    const search = await screen.findByRole('textbox', { name: '搜索知识库' })
    fireEvent.change(search, { target: { value: '识别号' } })
    fireEvent.click(screen.getByRole('button', { name: '搜索' }))
    expect(await screen.findByText(/识别号是 Q17/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '带到新对话' }))
    expect(await screen.findByRole('list', { name: '待发送附件' })).toHaveTextContent(file.name)
    expect(fetchMock.mock.calls.every(([url]) => String(url).includes('/workspace/documents?'))).toBe(true)
  })

  it('keeps earlier document questions and replies out of a later regular run', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, options?: RequestInit) => Response>(() => json({}))
    vi.stubGlobal('fetch', fetchMock)
    await createAgentRun([
      { id: '1', role: 'user', content: '普通问题', createdAt: file.uploaded_at },
      { id: '2', role: 'assistant', content: '普通回复', createdAt: file.uploaded_at },
      { id: '3', role: 'user', content: '私有资料的问题', createdAt: file.uploaded_at, documents: [file] },
      { id: '4', role: 'assistant', content: '私有资料的摘录', createdAt: file.uploaded_at },
      { id: '5', role: 'user', content: '下一条普通问题', createdAt: file.uploaded_at },
    ], 'workspace-1')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      conversation_id: 'workspace-1',
      messages: [
        { role: 'user', content: '普通问题' },
        { role: 'assistant', content: '普通回复' },
        { role: 'user', content: '下一条普通问题' },
      ],
    })
  })

  it('saves an actual data-source field and displays that activation needs a restart', async () => {
    window.history.replaceState(null, '', '/#/sources')
    const source = {
      id: 'local_catalog', name: '本地商品库', group: '购物采购', mode: 'disabled',
      description: '项目内置的合成商品资料', restart_required: false,
      fields: [{ id: 'taobao_tbk_local_catalog', label: '使用本地商品库', kind: 'switch', editable: true, configured: false }],
    }
    const fetchMock = vi.fn((_input: RequestInfo | URL, options?: RequestInit) =>
      options?.method === 'PATCH' ? json([{ ...source, restart_required: true }]) : json([source]),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    const procurement = await screen.findByRole('heading', { name: '购物采购' })
    expect(procurement.querySelector('[data-icon="shopping-cart"]')).toBeVisible()
    fireEvent.click(await screen.findByRole('button', { name: '配置 本地商品库' }))
    const modal = within(await screen.findByRole('dialog', { name: '配置 本地商品库' }))
    fireEvent.click(modal.getByRole('switch', { name: '使用本地商品库' }))
    fireEvent.click(modal.getByRole('button', { name: '保存配置' }))
    expect(await screen.findByText('配置已保存，重启后端后生效。')).toBeVisible()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/workspace\/sources\/local_catalog$/),
      expect.objectContaining({ method: 'PATCH', body: '{"taobao_tbk_local_catalog":true}' }),
    ))
  })
})
