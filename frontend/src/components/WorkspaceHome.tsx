import {
  BookOutlined, BulbOutlined, EnvironmentOutlined, HeartOutlined, MedicineBoxFilled,
  ReadFilled, RightOutlined, SendOutlined, ShoppingCartOutlined, ShoppingOutlined, SignalFilled,
} from '@ant-design/icons'
import { type ReactNode, useState } from 'react'

import './workspace-home.css'

const domains = [
  {
    id: 'seo', title: 'SEO / GEO', icon: SignalFilled,
    description: '页面诊断、关键词与 AI 引用核查',
    examples: [
      { title: '页面检查', detail: '核对标题和正文，定位优先改进项', prompt: '帮我检查 https://breadtopia.com/how-to-make-a-sourdough-starter/ 的标题和正文是否匹配，给我最优先的修改建议和依据。' },
      { title: '网页与考纲联查', detail: '对照原始公告与报名页', prompt: '请按医学人文修订通知核对 https://www.hbwsrc.cn/news/show-1830.html 中的适用考试类别。' },
    ],
  },
  {
    id: 'health', title: '医疗健康', icon: MedicineBoxFilled, sceneIcon: HeartOutlined,
    description: '医保目录、医学文献与复诊准备',
    examples: [
      { title: '复诊前准备清单', detail: '整理要带的资料、预约和想问医生的问题', prompt: '我下周要复诊，帮我整理要带的资料、已有的预约安排和想问医生的问题。' },
      { title: '医保药品目录', detail: '核查通用名、剂型和分类', prompt: '帮我核对国家医保药品目录当前生效版，二甲双胍有哪些常规剂型，分别是甲类还是乙类？' },
      { title: 'PubMed 文献检索', detail: '查公开题录和 PMID', prompt: '在 PubMed 上找 2024 至 2025 年关于青少年睡眠时长的 3 篇文献，列出题录和 PMID。' },
      { title: '医考报名', detail: '查湖北考区的技能免考条件', prompt: '我 2025 年实践技能考过了，但医学综合考试没过。2026 年在湖北报考医师资格，还要重新考技能和办理报名吗？' },
      { title: '考研招生对比', detail: '按专业代码对照两校目录', prompt: '比较 2026 年武汉科技大学 105101 和湖北民族大学 105100 的初试科目，请给出各自的招生目录出处。' },
    ],
  },
  {
    id: 'learning', title: '学习教育', icon: ReadFilled, sceneIcon: BookOutlined,
    description: '数学陪练、学习资源与报考备考',
    examples: [
      { title: '数学陪练', detail: '逐题练习方程，做完看解析', prompt: '我想练 3 道一元一次方程题，请一次出一道，做完给我看解析。' },
      { title: '教资笔试', detail: '查报名时间和官方备考大纲', prompt: '查一下 2026 年下半年湖北初中数学教资笔试的报名时间、考试科目和官方备考大纲。' },
      { title: '找官方练习', detail: '按年龄匹配学习资源', prompt: '9 岁学分数，有什么官方练习？' },
    ],
  },
  {
    id: 'travel', title: '商旅行程', icon: SendOutlined, sceneIcon: EnvironmentOutlined,
    description: '交通住宿、会场接驳与天气查询',
    examples: [
      { title: '比较出差方案', detail: '比较交通、住宿和会场通勤', prompt: '下周二从上海到武汉出差，周四返回。两人住一间房，预算 3500 元，周二下午 3 点前到武汉国际博览中心，预留 30 分钟。帮我比较高铁、飞机和酒店。' },
      { title: '查询出差天气', detail: '先看目的地天气和出行建议', prompt: '我去杭州出差。' },
    ],
  },
  {
    id: 'procurement', title: '购物采购', icon: ShoppingCartOutlined, sceneIcon: ShoppingOutlined,
    description: '商品查询、报价对比与采购清单',
    examples: [
      { title: '列活动采购清单', detail: '按人数和预算列出用品', prompt: '我给 20 人的活动准备纸杯、礼袋和桌牌，预算 800 元，帮我列采购清单和需要向商家确认的问题。' },
      {
        title: '比较商品报价',
        detail: '核算整包数量与已知费用',
        prompt: '请按两份报价核算实买数量和已知小计，我要 60 个 200ml 纸杯。\n甲商家：200ml 纸杯，50 个装，50 个起订，整包价 18 元。\n乙商家：200ml 纸杯，20 个装，20 个起订，整包价 7 元。',
      },
    ],
  },
] as const

export function WorkspaceHome({ composer, recent, onChoose }: {
  composer: ReactNode
  recent: ReactNode
  onChoose: (prompt: string) => void
}) {
  const [domainId, setDomainId] = useState<string>()
  const selected = domains.find(domain => domain.id === domainId)

  return (
    <div className="workbench-home">
      <div className="workbench-hero">
        <div className="workbench-intro">
          <h1>多领域 <span>Agent</span> 工作台</h1>
          <p>统一入口，按任务调用不同专业 Agent</p>
        </div>
        <img
          className="workbench-illustration"
          src={`${import.meta.env.BASE_URL}workbench-hero.webp`}
          width={1242}
          height={414}
          alt=""
          decoding="async"
          fetchPriority="high"
        />
      </div>
      <div className="workbench-composer">{composer}</div>
      <div className="workbench-domains" role="group" aria-label="业务入口">
        {domains.map(({ id, title, icon: Icon, description }) => (
          <button
            type="button"
            key={id}
            className={`workbench-domain workbench-tone-${id}`}
            aria-label={title}
            aria-expanded={selected?.id === id}
            aria-controls={selected?.id === id ? 'workspace-domain-examples' : undefined}
            onClick={() => setDomainId(selected?.id === id ? undefined : id)}
          >
            <span className="workbench-domain-title"><Icon aria-hidden="true" /><strong>{title}</strong></span>
            <span className="workbench-domain-description">
              <span>{description}</span><RightOutlined aria-hidden="true" />
            </span>
          </button>
        ))}
      </div>
      {selected && (
        <section id="workspace-domain-examples" className={`workbench-panel workbench-examples workbench-tone-${selected.id}`} aria-label={`${selected.title} 示例`}>
          {selected.examples.map(example => (
            <button type="button" key={example.title} onClick={() => { setDomainId(undefined); onChoose(example.prompt) }}>
              <strong>{example.title}</strong>
              <small>{example.detail}</small>
              <RightOutlined aria-hidden="true" />
            </button>
          ))}
        </section>
      )}
      {recent}
      <section className="workbench-panel workbench-scenes" aria-labelledby="workspace-scene-title">
        <h2 id="workspace-scene-title"><BulbOutlined aria-hidden="true" />推荐业务场景</h2>
        <div className="workbench-scene-grid">
          {domains.map(domain => {
            const Icon = 'sceneIcon' in domain ? domain.sceneIcon : domain.icon
            const example = domain.examples[0]
            return (
              <button type="button" className={`workbench-scene workbench-tone-${domain.id}`} key={domain.id} onClick={() => { setDomainId(undefined); onChoose(example.prompt) }}>
                <span><Icon aria-hidden="true" /><strong>{example.title}</strong></span>
                <small>{example.detail}</small>
              </button>
            )
          })}
        </div>
      </section>
    </div>
  )
}
