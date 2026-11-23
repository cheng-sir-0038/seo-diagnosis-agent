import { BookOutlined, CalculatorOutlined, FormOutlined, ProfileOutlined, SearchOutlined, SolutionOutlined } from '@ant-design/icons'

export const specialists = {
  healthcare: { name: '医药业务', icon: BookOutlined, tone: 'rose' },
  seo: { name: 'SEO / GEO', icon: SearchOutlined, tone: 'lavender' },
  learning: { name: '学习陪练', icon: CalculatorOutlined, tone: 'indigo' },
  exam_prep: { name: '报考备考', icon: FormOutlined, tone: 'amber' },
  followup: { name: '随访与复诊准备', icon: ProfileOutlined, tone: 'indigo' },
  travel: { name: '商旅行程', icon: SolutionOutlined, tone: 'lavender' },
} as const

export type Specialist = keyof typeof specialists

export const isSpecialist = (value: unknown): value is Specialist =>
  typeof value === 'string' && Object.hasOwn(specialists, value)

export const specialistStatusLabels = {
  running: '处理中',
  waiting_for_input: '待补充',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
} as const

export type SpecialistStatus = keyof typeof specialistStatusLabels
