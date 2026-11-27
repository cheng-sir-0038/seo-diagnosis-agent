import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import bundledSkillCatalog from '../services/bundledSkillCatalog.json'
import { loadProductConfig, saveProductConfig } from '../services/productApi'
import type { SpecialistDirectoryData } from '../types/agent'

const json = (data: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }))

const seoDirectory = {
  agent: 'seo', note: '指定网页后会载入该页可用的检查工具。',
  tools: [
    {
      name: 'seo_keyword_overview', description: '批量查询关键词搜索量、难度、点击和商业价值等指标。',
    },
    {
      name: 'seo_autocomplete', description: '查询 Google 搜索联想词，用于发现长尾词、问题词和用户真实表达。本工具不提供搜索量、难度、CPC 或排名。',
    },
    {
      name: 'seo_page_metadata', description: '直接读取当前站点页面源 HTML 中的 Title、Meta Description、Meta Keywords、Canonical、Robots、H1 和语言等元信息。用户询问当前页面 TDK 或标签内容时必须使用本工具；SERP 结果和 PageSpeed 不能替代本工具。',
    },
  ],
  skills: [
    { name: 'seo-keyword-overview', label: '关键词指标核对', summary: '提供 1–50 个关键词即可，无需网址。可指定查询市场和语言。', tools: ['seo_keyword_overview'] },
    { name: 'seo-autocomplete', label: '搜索联想词发现', summary: '提供一个种子词，可指定市场和语言。适合收集长尾词、问题词和搜索表达。', tools: ['seo_autocomplete'] },
    { name: 'seo-page-metadata', label: '网页内容与标签核对', summary: '提供具体网页，读取 HTML 中的标题、描述、正文证据及分享标签。', tools: ['seo_page_metadata'] },
  ],
  default_prompts: [
    { id: 'seo-diagnosis', label: 'SEO / GEO 诊断（静态节选）', text: '你是 SEO/GEO 专员。' },
    { id: 'seo-course-editor', label: '课程页与考纲核对', text: '将页面内容与考纲条款逐项比对。' },
    { id: 'seo-keyword-overview', label: '关键词指标核对（技能指令）', text: '在用户指定的市场和语言下批量核对搜索量、难度、点击和商业价值。保留词、市场和来源的对应关系；缺项标为无数据，不能按零计算或据此排序。' },
    { id: 'seo-autocomplete', label: '搜索联想词发现（技能指令）', text: '按用户给的种子词和语言查询搜索联想，保留有用的原始表达并去重。联想词只表示提议的搜索表达，不代表搜索量、难度、CPC 或真实排名。' },
    { id: 'seo-page-metadata', label: '网页内容与标签核对（技能指令）', text: '核对页面现状时读取指定 URL 的 HTML，沿用完整路径。只描述 HTML 中能验证的内容，不用 Lighthouse 或搜索结果代替标签事实。' },
  ],
} satisfies SpecialistDirectoryData['specialists'][number]

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

  it('keeps the global navigation width across pages and reloads', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json([])))
    const first = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '收起工作台导航' }))
    expect(screen.getByRole('button', { name: '展开工作台导航' })).toHaveAttribute('aria-expanded', 'false')
    first.unmount()
    window.history.replaceState(null, '', '/#/tasks')
    render(<App />)
    expect(await screen.findByRole('heading', { name: '任务工作台' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '展开工作台导航' }))
    expect(screen.getByRole('button', { name: '收起工作台导航' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('restores the current page, reading position, and chat draft through browser history', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json([])))
    render(<App />)
    const chat = screen.getByRole('link', { name: '对话工作台' })
    const tasks = screen.getByRole('link', { name: '任务工作台' })
    const history = screen.getByRole('link', { name: '历史会话' })
    const draft = screen.getByRole('textbox', { name: '给业务助理发送消息' })
    expect(chat).toHaveAttribute('aria-current', 'page')
    fireEvent.change(draft, { target: { value: '保留这段草稿' } })

    fireEvent.click(tasks)
    await screen.findByRole('heading', { name: '任务工作台' })
    const page = screen.getByRole('region', { name: '任务工作台' })
    expect(page).toHaveFocus()
    expect(tasks).toHaveAttribute('aria-current', 'page')
    page.querySelector('.workspace-scroll')!.scrollTop = 180
    fireEvent.click(history)
    await screen.findByRole('heading', { name: '历史会话' })

    window.history.back()
    await screen.findByRole('heading', { name: '任务工作台' })
    expect(tasks).toHaveAttribute('aria-current', 'page')
    expect(page.querySelector('.workspace-scroll')!.scrollTop).toBe(180)
    window.history.back()
    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toHaveValue('保留这段草稿')
    window.history.forward()
    await screen.findByRole('heading', { name: '任务工作台' })
    expect(chat).not.toHaveAttribute('aria-current')
  })

  it('shows the active text model and saves a provider selection for the next restart', async () => {
    const source = {
      id: 'agent_model', name: '主对话与 SEO 文本', group: '模型', description: '模型配置',
      mode: 'configured', restart_required: false,
      text_model: { provider: 'openai', model: 'openai-current', configured: true },
      fields: [{
        id: 'agent_model_provider', label: '服务选择', kind: 'choice', editable: true, configured: true,
        value: 'auto', options: [{ value: 'auto', label: '自动（优先 OpenAI）' }, { value: 'deepseek', label: 'DeepSeek' }],
      }],
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH' && String(input).endsWith('/workspace/sources/agent_model')) {
        return json([{
          ...source, restart_required: true,
          pending_text_model: { provider: 'deepseek', model: 'deepseek-next', configured: true },
          fields: [{ ...source.fields[0], value: 'deepseek' }],
        }])
      }
      return json(String(input).endsWith('/workspace/sources') ? [source] : [])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.click(screen.getByRole('link', { name: '数据源管理' }))
    expect(await screen.findByText('当前模型：OpenAI / openai-current')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '配置 主对话与 SEO 文本' }))
    const dialog = within(await screen.findByRole('dialog', { name: '配置 主对话与 SEO 文本' }))
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '服务选择' }))
    fireEvent.click(await screen.findByText('DeepSeek', { exact: true }))
    fireEvent.click(dialog.getByRole('button', { name: '保存配置' }))
    expect(await screen.findByText('重启后：DeepSeek / deepseek-next')).toBeVisible()
    expect(screen.getByText('当前模型：OpenAI / openai-current')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/workspace/sources/agent_model'), expect.objectContaining({
      method: 'PATCH', body: JSON.stringify({ agent_model_provider: 'deepseek' }),
    }))
  })

  it('recognizes the running center with fragment query parameters and preserves existing deep links', async () => {
    window.history.replaceState(null, '', '/#/observability?view=issues')
    vi.stubGlobal('fetch', vi.fn(() => json([])))
    render(<App />)
    const link = screen.getByRole('link', { name: '运行中心' })
    const pane = screen.getByRole('region', { name: '运行中心' }).querySelector('.workspace-scroll')
    expect(link).toHaveAttribute('aria-current', 'page')
    expect(link).toHaveAttribute('href', '#/observability?view=issues')
    fireEvent.click(link)
    expect(window.location.hash).toBe('#/observability?view=issues')

    window.location.hash = '#/observability?view=traces&trace=example'
    await waitFor(() => expect(link).toHaveAttribute('href', '#/observability?view=traces&trace=example'))
    expect(screen.getByRole('region', { name: '运行中心' }).querySelector('.workspace-scroll')).toBe(pane)
    window.history.back()
    await waitFor(() => expect(link).toHaveAttribute('href', '#/observability?view=issues'))
  })

  it('closes the mobile menu with Escape or a page link and moves focus to the expected target', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json([])))
    render(<App />)
    const trigger = screen.getByRole('button', { name: '打开工作台菜单' })
    fireEvent.click(trigger)
    const menu = screen.getByRole('dialog', { name: '工作台菜单' })
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(within(menu).getByRole('link', { name: '对话工作台' })).toHaveFocus()
    const close = within(menu).getByRole('button', { name: '关闭工作台菜单' })
    close.focus()
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(within(menu).getByRole('link', { name: '数据源管理' })).toHaveFocus()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(menu).not.toHaveAttribute('role', 'dialog')
    expect(trigger).toHaveFocus()

    fireEvent.click(trigger)
    fireEvent.click(within(menu).getByRole('link', { name: '任务工作台' }))
    await screen.findByRole('heading', { name: '任务工作台' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('region', { name: '任务工作台' })).toHaveFocus()
  })

  it('opens chat in a fresh browser and makes no site or actor requests', async () => {
    const fetchMock = vi.fn(() => json([]))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    const entries = within(screen.getByRole('group', { name: '业务入口' }))
    expect(entries.getAllByRole('button')).toHaveLength(5)
    fireEvent.click(entries.getByRole('button', { name: '医疗健康' }))
    const health = within(screen.getByRole('region', { name: '医疗健康 示例' }))
    expect(health.getByRole('button', { name: /医保药品目录/ })).toBeInTheDocument()
    expect(health.getByRole('button', { name: /PubMed 文献检索/ })).toBeInTheDocument()
    expect(health.getByRole('button', { name: /医考报名/ })).toBeInTheDocument()
    expect(health.getByRole('button', { name: /考研招生对比/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'SEO 站点登记' })).not.toBeInTheDocument()
    fireEvent.click(entries.getByRole('button', { name: 'SEO / GEO' }))
    fireEvent.click(screen.getByRole('button', { name: /网页与考纲联查/ }))
    expect((screen.getByLabelText('给业务助理发送消息') as HTMLTextAreaElement).value).toContain('www.hbwsrc.cn/')
    expect(screen.queryByLabelText('站点域名')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('opens the Agent abilities page from history, retries loading, and switches specialists inline', async () => {
    window.history.replaceState(null, '', '/#/history')
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => json({}, 503))
      .mockImplementationOnce(() => json({ specialists: [
        {
          agent: 'healthcare', skill_catalog_version: 1, tools: [{
            name: 'medical_exam_syllabus', description: '核对官方考纲',
            public_prompt_ids: ['healthcare-instructions'], skill_usage: {
              scenario: '核对本次考纲的适用范围。', inputs: '提供所问版次。',
              rules: '按原公告核对。', output: '列出公告和出处。',
            },
          }],
          skills: [{
            name: 'medical-exam-syllabus', label: '医考考纲核对', summary: '核对考纲的适用范围。', tools: ['medical_exam_syllabus'],
          }],
          default_prompts: [
            { id: 'medical-exam-syllabus', label: '考纲指令', text: '以公告原文为准。\n<source>待核实</source>' },
            { id: 'healthcare-instructions', label: '医药共用指令', text: '保留官方资料出处。' },
          ],
        },
        seoDirectory,
        { agent: 'travel', skills: [], default_prompts: [], tools: [{
          name: 'travel_transport', description: '查询班次。', availability_note: '飞猪实时查询未配置。',
        }] },
        {
          agent: 'followup',
          tools: [
            { name: 'followup_read_materials', description: '读取资料' },
            { name: 'followup_prepare_materials', description: '整理资料' },
          ],
          skills: [{
            name: 'followup-preparation', label: '随访与复诊资料整理', summary: '检查资料是否齐全。',
            tools: ['followup_read_materials', 'followup_prepare_materials'],
          }],
          default_prompts: [],
          deterministic_path: '资料整理按固定规则执行，无专员提示词。',
        },
      ] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(fetchMock).not.toHaveBeenCalled()
    const entrance = screen.getByRole('link', { name: 'Agent 能力' })
    fireEvent.click(entrance)
    expect(await screen.findByRole('heading', { name: 'Agent 能力', level: 1 })).toBeVisible()
    expect(window.location.hash).toBe('#/capabilities')
    expect(entrance).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '重试' }))
    expect(await screen.findByText('查证考纲条款')).toBeVisible()
    const health = within(screen.getByRole('region', { name: '医药业务' }))
    expect(health.getByText('核对本次考纲的适用范围。')).not.toBeVisible()
    expect(health.getByText('Skill：医考考纲核对')).not.toBeVisible()
    expect(health.getByText('医药共用指令')).not.toBeVisible()
    expect(health.queryByText('技能', { exact: true })).not.toBeInTheDocument()
    expect(health.queryByText('默认提示词', { exact: true })).not.toBeInTheDocument()
    fireEvent.click(health.getByText('查证考纲条款'))
    expect(health.getByText('核对本次考纲的适用范围。')).toBeVisible()
    const prompt = health.getByRole('region', { name: '查证考纲条款的 Skill 详情' })
    const rawSyllabus = within(prompt).getByText('原始 Skill 指令')
    expect(within(prompt).getByText(/以公告原文为准/)).not.toBeVisible()
    fireEvent.click(rawSyllabus)
    expect(within(prompt).getByText(/以公告原文为准/)).toBeVisible()
    expect(prompt.querySelector('pre')?.textContent).toBe('以公告原文为准。\n<source>待核实</source>')
    expect(within(prompt).getByText('医药共用指令')).toBeVisible()
    expect(within(prompt).getByText('保留官方资料出处。')).not.toBeVisible()
    fireEvent.click(within(prompt).getByText('医药共用指令'))
    expect(within(prompt).getByText('保留官方资料出处。')).toBeVisible()
    expect(prompt.querySelector('source')).toBeNull()
    expect(health.queryByRole('textbox')).not.toBeInTheDocument()
    const choices = within(screen.getByRole('group', { name: '专员' }))
    expect(choices.getByRole('button', { name: /医药业务/ })).toHaveAttribute('aria-pressed', 'true')
    expect(choices.getByRole('button', { name: /医药业务.*1 项能力/ })).toBeVisible()
    const seo = choices.getByRole('button', { name: /SEO \/ GEO/ })
    fireEvent.click(seo)
    expect(seo).toHaveAttribute('aria-pressed', 'true')
    const seoTools = screen.getByRole('region', { name: 'SEO / GEO' })
    expect(seoTools).toHaveTextContent('指定网页后会载入该页可用的检查工具。')
    const seoSkills = within(seoTools)
    expect(seoSkills.queryByRole('status')).not.toBeInTheDocument()
    expect(seoSkills.getByText(/^提供 1–50 个关键词。/)).not.toBeVisible()
    expect(seoSkills.getByText('Skill：关键词指标核对')).not.toBeVisible()
    fireEvent.click(seoSkills.getByText('查询关键词指标'))
    const keyword = within(seoSkills.getByRole('region', { name: '查询关键词指标的 Skill 详情' }))
    expect(seoSkills.getByText(/^提供 1–50 个关键词。/)).toBeVisible()
    expect(keyword.getByRole('heading', { name: 'Skill：关键词指标核对' })).toBeVisible()
    expect(keyword.getByText('提供 1–50 个关键词。可指定市场和语言，无需网址。')).toBeVisible()
    expect(keyword.getByText(/^逐词给出可查到的搜索量/)).toBeVisible()
    expect(keyword.getByText('处理规则')).toBeVisible()
    expect(keyword.queryByText('按用户给的种子词和语言查询搜索联想，保留有用的原始表达并去重。联想词只表示提议的搜索表达，不代表搜索量、难度、CPC 或真实排名。')).not.toBeInTheDocument()
    expect(seoSkills.getByText(/^提供一个种子词。可指定市场和语言。/)).not.toBeVisible()
    fireEvent.click(seoSkills.getByText('查询搜索联想词'))
    const autocomplete = within(seoSkills.getByRole('region', { name: '查询搜索联想词的 Skill 详情' }))
    expect(autocomplete.getByRole('heading', { name: 'Skill：搜索联想词发现' })).toBeVisible()
    expect(autocomplete.getByText(/^给出本次搜到的联想表达/)).toBeVisible()
    expect(autocomplete.queryByText('在用户指定的市场和语言下批量核对搜索量、难度、点击和商业价值。保留词、市场和来源的对应关系；缺项标为无数据，不能按零计算或据此排序。')).not.toBeInTheDocument()
    expect(keyword.queryByText('课程页与考纲核对')).not.toBeInTheDocument()
    expect(autocomplete.queryByText('课程页与考纲核对')).not.toBeInTheDocument()
    fireEvent.click(seoSkills.getByText('读取网页内容'))
    const webpage = within(seoSkills.getByRole('region', { name: '读取网页内容的 Skill 详情' }))
    expect(webpage.getByRole('heading', { name: 'Skill：网页内容与标签核对' })).toBeVisible()
    fireEvent.click(webpage.getByText('课程页与考纲核对'))
    expect(webpage.getByText('仅在结合考纲核对课程页时使用。')).toBeVisible()
    const coursePrompt = bundledSkillCatalog.instructions.find(prompt => prompt.id === 'seo-course-editor')!
    expect(webpage.getByText(coursePrompt.text, { normalizer: text => text })).toBeVisible()
    expect(seoTools).not.toHaveTextContent('SEO / GEO 诊断（静态节选）')
    expect(seoTools).not.toHaveTextContent('seo_keyword_overview')
    fireEvent.click(choices.getByRole('button', { name: /商旅/ }))
    expect(screen.getByText('实时查询不可用')).toBeVisible()
    fireEvent.click(screen.getByText('查询班次。'))
    expect(screen.getByText(/飞猪实时查询未配置/)).toBeVisible()
    expect(screen.getByRole('link', { name: '查看数据源' })).toHaveAttribute('href', '#/sources')
    expect(screen.queryByText('查证考纲条款')).not.toBeInTheDocument()
    fireEvent.click(choices.getByRole('button', { name: /随访与复诊/ }))
    expect(screen.getAllByText('资料整理按固定规则执行，无专员提示词。').every(node => !node.closest('details')?.open)).toBe(true)
    fireEvent.click(screen.getByText('整理复诊前准备清单'))
    expect(screen.getByText(/^生成带文件和解析行的清单草稿/)).toBeVisible()
    expect(screen.getByText(/^统计有效记录和未读入的文件/)).not.toBeVisible()
    expect(screen.getAllByText('资料整理按固定规则执行，无专员提示词。').filter(node => node.closest('details')?.open)).toHaveLength(1)
    fireEvent.click(choices.getByRole('button', { name: /医药业务/ }))
    expect(screen.getByText('医药共用指令')).not.toBeVisible()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.every(call => String(call[0]).includes('/api/v1/chat/tools?conversation_id='))).toBe(true)
  })

  it('uses the approved Skill body and shared text even when legacy healthcare sends router fields', async () => {
    window.history.replaceState(null, '', '/#/capabilities')
    vi.stubGlobal('fetch', vi.fn(() => json({ specialists: [{
      agent: 'healthcare',
      tools: [{ name: 'medical_exam_registration', description: '报名核对' }],
      skills: [{ name: 'medical-exam-registration', label: '医考报名核对', tools: ['medical_exam_registration'] }],
      default_prompts: [
        { id: 'medical-exam-registration', label: '医考报名核对', text: '按 registration 和 skills_retake 选择。' },
        { id: 'healthcare-instructions', label: '医药共用指令', text: '收到 clarification_received 后以 confirmed 重试。' },
      ],
    }] })))
    render(<App />)
    fireEvent.click(await screen.findByText('核对医考报名条件'))
    const details = within(screen.getByRole('region', { name: '核对医考报名条件的 Skill 详情' }))
    expect(details.getByRole('heading', { name: 'Skill：医考报名核对' })).toBeVisible()
    expect(details.getByText(/^查网上报名.*实践技能考试免考核对/)).toBeVisible()
    expect(details.getByText(/两种核对均需报考年份/)).toBeVisible()
    expect(details.getByText(/符合条件时免实践技能考试/)).toBeVisible()
    const original = details.getByText(/实践技能考试免考核对按上次技能考试年份/)
    expect(original).not.toBeVisible()
    fireEvent.click(details.getByText('原始 Skill 指令'))
    expect(original).toBeVisible()
    fireEvent.click(details.getByText('医药共用指令'))
    expect(details.getByText(/采用用户更正后的条件/)).toBeVisible()
    expect(details.queryByText(/registration|skills_retake|clarification_received|confirmed/)).not.toBeInTheDocument()
    expect(screen.queryByText('当前工具目录的数据版本未提供完整的 Skill 详情。')).not.toBeInTheDocument()
  })

  it.each([
    ['seo', 'seo_page_metadata', '读取网页内容'],
    ['healthcare', 'medical_exam_registration', '核对医考报名条件'],
  ])('does not display unversioned %s text which has no approved binding', async (agent, tool, label) => {
    window.history.replaceState(null, '', '/#/capabilities')
    const name = 'legacy-public-review'
    const instruction = '核对公开条款。\n保留原始行。'
    vi.stubGlobal('fetch', vi.fn(() => json({ specialists: [{
      agent, tools: [{ name: tool, description: '核对公开信息。' }],
      skills: [{ name, label: '核对条款', tools: [tool] }],
      default_prompts: [{ id: name, label: '核对条款', text: instruction }],
    }] })))
    render(<App />)
    expect((await screen.findByText(label)).closest('summary')).toBeNull()
    expect(screen.queryByText(/保留原始行/)).not.toBeInTheDocument()
  })

  it('does not borrow instructions for an unrecognized tool binding', async () => {
    window.history.replaceState(null, '', '/#/capabilities')
    vi.stubGlobal('fetch', vi.fn(() => json({ specialists: [{
      agent: 'healthcare',
      tools: [{ name: 'medical_exam_registration', description: '报名核对' }],
      skills: [{ name: 'medical-exam-syllabus', label: '医考考纲核对', tools: ['medical_exam_registration'] }],
    }] })))
    render(<App />)
    expect((await screen.findByText('核对医考报名条件')).closest('summary')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('当前工具目录的数据版本未提供完整的 Skill 详情。')
  })

  it('expands registered SEO tools from a legacy directory and leaves unknown rows without empty disclosures', async () => {
    window.history.replaceState(null, '', '/#/capabilities')
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => json({ specialists: [{
        agent: 'seo', skills: [], tools: [
          { ...seoDirectory.tools[0], skill_usage: null },
          { name: 'seo_unrecognized_tool', description: '尚无说明的工具。' },
        ],
      }] }))
      .mockImplementationOnce(() => json({ specialists: [
        { agent: 'healthcare', tools: [], skills: [], default_prompts: [] }, seoDirectory,
      ] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    const directory = await screen.findByRole('region', { name: 'SEO / GEO' })
    const legacy = within(directory)
    expect(legacy.getByRole('status')).toHaveTextContent('当前工具目录的数据版本未提供完整的 Skill 详情。')
    const keyword = legacy.getByText('查询关键词指标')
    expect(keyword).toBeVisible()
    expect(legacy.getByText('批量查询关键词搜索量、难度、点击和商业价值等指标。')).toBeVisible()
    expect(legacy.getByText('尚无说明的工具。').closest('summary')).toBeNull()
    expect(legacy.queryByText('查询搜索联想词')).not.toBeInTheDocument()
    fireEvent.click(keyword)
    const skill = within(legacy.getByRole('region', { name: '查询关键词指标的 Skill 详情' }))
    expect(skill.getByRole('heading', { name: 'Skill：关键词指标核对' })).toBeVisible()
    expect(skill.getByText(/^提供 1–50 个关键词。/)).toBeVisible()
    expect(directory).not.toHaveTextContent('seo_keyword_overview')

    fireEvent.click(legacy.getByRole('button', { name: '重新加载工具目录' }))
    await waitFor(() => expect(screen.getByText('查询关键词指标').closest('summary')).not.toBeNull())
    expect(screen.queryByText('当前工具目录的数据版本未提供完整的 Skill 详情。')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('/api/v1/chat/tools?conversation_id='), expect.objectContaining({ cache: 'no-store' }))
  })

  it.each([[], undefined].map(ids => ({ ids })))('renders only declared versioned tool bindings ($ids)', async ({ ids }) => {
    window.history.replaceState(null, '', '/#/capabilities')
    const instruction = '仅核对已有档案。\n<source>原始记录</source>'
    vi.stubGlobal('fetch', vi.fn(() => json({ specialists: [
      {
        agent: 'archive_review', skill_catalog_version: 1,
        profile: { name: '档案核验', icon: 'book', tone: 'indigo', directory_tone: 'learning' },
        tools: [
          { name: 'archive_scan', description: '核对档案编号。', public_prompt_ids: ['archive-sources'] },
          { name: 'archive_index', description: '整理档案索引。' },
        ],
        skills: [
          { name: 'archive-scan', label: '核对档案', tools: ['archive_scan'] },
          { name: 'archive-index', label: '索引档案', summary: '按原始编号整理。', tools: ['archive_index'] },
        ],
        default_prompts: [
          { id: 'archive-scan', label: '核对档案', text: instruction },
          { id: 'archive-sources', label: '核对来源', text: '保留档案编号。' },
          { id: 'unbound', label: '未绑定的指令', text: '不可显示。' },
        ],
      },
      {
        ...seoDirectory, skill_catalog_version: 1, skills: [],
        tools: [{ ...seoDirectory.tools[2], public_prompt_ids: ids }],
      },
      { agent: 'supervisor', tools: [] },
    ] })))
    render(<App />)
    const choices = within(await screen.findByRole('group', { name: '专员' }))
    const choice = choices.getByRole('button', { name: /档案核验/ })
    expect(choice).toHaveClass('workbench-tone-learning')
    expect(choice.querySelector('[data-icon="book"]')).toBeVisible()
    expect(choices.getAllByRole('button')).toHaveLength(2)
    const archive = within(screen.getByRole('region', { name: '档案核验' }))
    fireEvent.click(archive.getByLabelText('核对档案编号。'))
    const details = archive.getByRole('region', { name: '核对档案编号。的 Skill 详情' })
    expect(within(details).getByRole('heading', { name: 'Skill：核对档案' })).toBeVisible()
    expect(details.querySelector('pre')?.textContent).toBe(instruction)
    expect(details.querySelector('source')).toBeNull()
    expect(within(details).getByText('核对来源')).toBeVisible()
    expect(archive.queryByText('未绑定的指令')).not.toBeInTheDocument()

    fireEvent.click(archive.getByLabelText('整理档案索引。'))
    const index = within(archive.getByRole('region', { name: '整理档案索引。的 Skill 详情' }))
    expect(index.getByRole('heading', { name: 'Skill：索引档案' })).toBeVisible()
    expect(index.getByText('按原始编号整理。')).toBeVisible()
    expect(index.queryByText('原始 Skill 指令')).not.toBeInTheDocument()

    fireEvent.click(choices.getByRole('button', { name: /SEO \/ GEO/ }))
    const seo = within(screen.getByRole('region', { name: 'SEO / GEO' }))
    expect(seo.getByText('读取网页内容').closest('summary')).toBeNull()
    expect(seo.queryByText('课程页与考纲核对')).not.toBeInTheDocument()
    expect(seo.queryByRole('status')).not.toBeInTheDocument()
  })

  it('never renders the Travel router fragment in a legacy Skill row', async () => {
    window.history.replaceState(null, '', '/#/capabilities')
    const routerText = 'travel 处理国内单目的地的单程或往返交通、住宿与到访地点安排，核对时间预算并保存用户选择。即使缺日期或城市，也先派给 travel，保留明确的条件。用户贴的报价和链接是行程资料。想做旅游站点的 SEO 仍归 seo。'
    const name = 'business-trip-planning'
    vi.stubGlobal('fetch', vi.fn(() => json({ specialists: [{
      agent: 'travel',
      tools: [{ name: 'travel_transport', description: '查询班次。' }],
      skills: [{ name, label: '国内商旅行程', tools: ['travel_transport'] }],
      default_prompts: [
        { id: name, label: '商旅分派指令', text: routerText },
        { id: 'travel-intake', label: '商旅条件提取', text: '保留已确认的条件。' },
      ],
    }] })))
    render(<App />)
    fireEvent.click(await screen.findByLabelText('查询高铁与航班'))
    const details = screen.getByRole('region', { name: '查询高铁与航班的 Skill 详情' })
    const row = within(details)
    const bundled = bundledSkillCatalog.skills.find(skill => skill.agent === 'travel' && skill.name === name
      && skill.tools.includes('travel_transport'))!
    expect(row.getByRole('heading', { name: `Skill：${bundled.label}` })).toBeVisible()
    expect(row.getByText('适用场景')).toBeVisible()
    expect(row.getByText('商旅条件提取')).toBeVisible()
    const disclosure = row.getByText('原始 Skill 指令')
    fireEvent.click(disclosure)
    const instruction = disclosure.closest('details')?.querySelector('pre')
    expect(instruction).toBeVisible()
    expect(instruction?.textContent).toBe(bundled.instruction)
    expect(row.queryByText('保留已确认的条件。', { exact: true })).not.toBeInTheDocument()
    expect(details).not.toHaveTextContent(/travel 处理国内单目的地|先派给 travel/)
  })

  it('filters tasks from the saved browser conversations and opens the original waiting run', async () => {
    window.history.replaceState(null, '', '/#/tasks')
    const now = new Date().toISOString()
    localStorage.setItem('agent-workspace-conversations-v2', JSON.stringify([{
      id: 'local', title: '本地会话', updatedAt: now, messages: [
        { id: 'question', role: 'user', content: '比较出差方案', createdAt: now },
        {
          id: 'answer', role: 'assistant', content: '可以先确认日期。', createdAt: now,
          runId: 'waiting', inputRequest: { requestId: 'dates', questions: ['哪天出发？'] },
        },
      ],
    }]))
    const fetchMock = vi.fn(() => json([
      { id: 'task-waiting', conversation_id: 'local', current_run_id: 'waiting', title: '核对行程日期', status: 'WAITING_FOR_INPUT', created_at: now, updated_at: now },
      { id: 'task-running', conversation_id: 'local', current_run_id: 'another', title: '查询天气', status: 'RUNNING', created_at: now, updated_at: now },
      { id: 'task-other', conversation_id: 'other', current_run_id: 'private', title: '其他会话任务', status: 'RUNNING', created_at: now, updated_at: now },
    ]))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    const tasks = within(screen.getByRole('region', { name: '任务列表' }))
    expect(await tasks.findByRole('button', { name: /核对行程日期/ })).toBeVisible()
    expect(tasks.getByRole('button', { name: /查询天气/ })).toBeVisible()
    expect(tasks.queryByRole('button', { name: /其他会话任务/ })).not.toBeInTheDocument()
    fireEvent.click(tasks.getByRole('radio', { name: '等待补充' }))
    expect(tasks.queryByRole('button', { name: /查询天气/ })).not.toBeInTheDocument()
    fireEvent.click(tasks.getByRole('button', { name: /核对行程日期/ }))
    expect(await screen.findByRole('heading', { name: '核对行程日期' })).toBeInTheDocument()
    expect(screen.getByText('哪天出发？')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/v1/chat/tasks?conversation_id=local'), expect.anything())
  })

  it('validates registration from its direct URL and can return to chat', async () => {
    const fetchMock = vi.fn(() => json([]))
    vi.stubGlobal('fetch', fetchMock)
    window.history.replaceState(null, '', '/#/site-setup')
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: '登记站点' }, { timeout: 3000 }))

    expect(await screen.findByText('请检查标记的字段后重新提交。')).toBeInTheDocument()
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
    let registrationAttempts = 0
    const oldConfig = { siteId: 'old-site', ownerActorId: 'old-owner', agentActorId: 'old-agent', executorActorId: 'old-executor' }
    saveProductConfig(oldConfig)
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/v1/seo-agent/system/site-registrations') && init?.method === 'POST') {
        if (++registrationAttempts === 1) return json({ detail: '暂时无法完成登记，请重试。' }, 503)
        return json({ site, owner_actor_id: 'owner', agent_actor_id: 'agent', executor_actor_id: 'executor' }, 201)
      }
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.change(await screen.findByLabelText('站点域名'), { target: { value: '  https://EXAMPLE.com/path  ' } })
    fireEvent.change(screen.getByLabelText('负责人邮箱'), { target: { value: 'owner@example.com' } })
    fireEvent.change(screen.getByLabelText('执行者邮箱'), { target: { value: 'writer@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '登记站点' }))

    expect(await screen.findByText('暂时无法完成登记，请重试。')).toBeVisible()
    expect(window.location.hash).toBe('#/site-setup')
    expect(loadProductConfig()).toEqual(oldConfig)
    expect(screen.getByLabelText('站点域名')).toHaveValue('  https://EXAMPLE.com/path  ')
    fireEvent.click(screen.getByRole('button', { name: '登记站点' }))

    expect(await screen.findByRole('textbox', { name: '给业务助理发送消息' })).toBeEnabled()
    expect(loadProductConfig()).toEqual({ siteId: 'site-new', ownerActorId: 'owner', agentActorId: 'agent', executorActorId: 'executor' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      domain: 'https://EXAMPLE.com/path', default_country: 'US', default_language: 'en-US',
      owner_email: 'owner@example.com', executor_email: 'writer@example.com',
    })
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
