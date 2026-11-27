import { BookOutlined, CalculatorOutlined, FormOutlined, ProfileOutlined, SearchOutlined, ShoppingCartOutlined, SolutionOutlined } from '@ant-design/icons'

export type Specialist = string

export interface SpecialistProfile {
  name: string
  icon: string
  tone: string
  directory_tone: string
}

const reservedIds = new Set(['supervisor', 'clarify', 'course_review'])

export const isSpecialist = (value: unknown): value is Specialist =>
  typeof value === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(value) && !reservedIds.has(value)

const legacyProfiles: Record<string, SpecialistProfile> = {
  healthcare: { name: '医药业务', icon: 'book', tone: 'rose', directory_tone: 'health' },
  seo: { name: 'SEO / GEO', icon: 'search', tone: 'lavender', directory_tone: 'seo' },
  learning: { name: '学习陪练', icon: 'calculator', tone: 'indigo', directory_tone: 'learning' },
  exam_prep: { name: '报考备考', icon: 'form', tone: 'amber', directory_tone: 'learning' },
  followup: { name: '随访与复诊准备', icon: 'profile', tone: 'indigo', directory_tone: 'health' },
  travel: { name: '商旅行程', icon: 'travel', tone: 'lavender', directory_tone: 'travel' },
  shopping_procurement: { name: '购物与采购', icon: 'cart', tone: 'amber', directory_tone: 'procurement' },
}

const icons: Record<string, typeof ProfileOutlined> = {
  book: BookOutlined,
  search: SearchOutlined,
  calculator: CalculatorOutlined,
  form: FormOutlined,
  profile: ProfileOutlined,
  travel: SolutionOutlined,
  cart: ShoppingCartOutlined,
}

const runTones = new Set(['rose', 'lavender', 'indigo', 'amber'])
const directoryTones = new Set(['seo', 'health', 'learning', 'travel', 'procurement'])

export function readSpecialistProfile(value: unknown): SpecialistProfile | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const { name, icon, tone, directory_tone } = value as Record<string, unknown>
  if (typeof name !== 'string' || !name.trim() || typeof icon !== 'string'
    || typeof tone !== 'string' || typeof directory_tone !== 'string') return undefined
  return { name, icon, tone, directory_tone }
}

export function specialistPresentation(agent: unknown, value?: unknown) {
  const id = isSpecialist(agent) ? agent : undefined
  const profile = readSpecialistProfile(value) ?? (id && Object.hasOwn(legacyProfiles, id) ? legacyProfiles[id] : undefined)
  return {
    name: profile?.name ?? id ?? '业务专员',
    icon: profile && Object.hasOwn(icons, profile.icon) ? icons[profile.icon] : ProfileOutlined,
    tone: profile && runTones.has(profile.tone) ? profile.tone : 'neutral',
    directoryTone: profile && directoryTones.has(profile.directory_tone) ? profile.directory_tone : 'neutral',
  }
}

export const specialistStatusLabels = {
  running: '处理中',
  waiting_for_input: '待补充',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
} as const

export type SpecialistStatus = keyof typeof specialistStatusLabels
