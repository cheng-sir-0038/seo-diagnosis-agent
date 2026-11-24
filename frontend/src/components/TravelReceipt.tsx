import {
  ArrowRightOutlined, BankOutlined, CalendarOutlined, CarOutlined, CheckCircleOutlined, CloudDownloadOutlined,
  CloudOutlined, CompassOutlined, EnvironmentOutlined, ExportOutlined, SendOutlined, SunOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { Segmented, Select, Tabs } from 'antd'
import { useState } from 'react'

import type { ToolRun } from '../types/agent'
import { BaiduRouteMap, type RouteGeometry } from './BaiduRouteMap'
import { ResultTag } from './ResultTag'
import './TravelReceipt.css'

type Amount = number | string
type TravelCostKind = 'outbound' | 'return' | 'hotel' | 'ground'
type CostItem = {
  status: 'no_offer' | 'price_unconfirmed' | 'stay_unconfirmed' | 'partial' | 'included'
  amount?: Amount
  currency?: string
}

interface Offer {
  id: string
  name: string
  source: string
  kind?: 'transport' | 'hotel'
  direction?: Exclude<TravelCostKind, 'ground'>
  mode?: string | null
  address?: string | null
  from_stop?: string | null
  to_stop?: string | null
  departs_at?: string | null
  arrives_at?: string | null
  departs_local?: string | null
  arrives_local?: string | null
  check_in?: string | null
  check_out?: string | null
  room_type?: string | null
  fare_class?: string | null
  amount?: Amount | null
  currency?: string | null
  price_basis?: string | null
  full_price?: boolean
  booking_url?: string | null
  queried_at?: string
}

interface TravelPlan {
  id: string
  label: string
  transport?: Offer | string | null
  return_transport?: Offer | string | null
  hotel?: Offer | string | null
  known_cost?: Amount | null
  known_costs?: Record<string, Amount>
  cost_items?: Partial<Record<TravelCostKind, CostItem>>
  currency?: string | null
  complete_price?: boolean
  missing?: string[]
  warnings?: string[]
  hotel_commute_minutes?: number | null
  arrival_commute_minutes?: number | null
  arrival_at_venue?: string | null
  arrival_status?: string
  budget_status?: string
}

interface Provider {
  source: string
  label?: string
  status: string
  message?: string | null
  queried_at?: string
}

interface Weather extends Provider {
  location?: string | null
  requested_city?: string
  timezone?: string | null
  scope?: 'itinerary' | 'near_term'
  advice?: string[]
  items?: {
    date: string
    weather_code?: number | null
    temperature_min_c?: number | null
    temperature_max_c?: number | null
    precipitation_probability_max_pct?: number | null
    wind_speed_max_kmh?: number | null
  }[]
}

interface TravelRoute extends Provider {
  origin?: string
  destination?: string
  source_url?: string
  items?: { duration_minutes?: number; distance_meters?: number }[]
  map?: RouteGeometry
}

interface TravelData {
  request: {
    origin?: string | null
    destination?: string | null
    depart_on?: string | null
    return_on?: string | null
    venue?: string | null
    arrive_by?: string | null
    budget_cny?: Amount | null
    rooms?: number | null
    travelers?: number | null
    buffer_minutes?: number | null
  }
  plans: TravelPlan[]
  offers?: Offer[]
  issues?: string[]
  providers?: Provider[]
  routes?: Record<string, TravelRoute>
  weather?: Weather | null
  selected_plan_id?: string | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

function isTravelData(value: unknown): value is TravelData {
  return isRecord(value) && isRecord(value.request) && Array.isArray(value.plans)
    && value.plans.every(plan => isRecord(plan) && typeof plan.id === 'string' && typeof plan.label === 'string')
}

function isWeather(value: unknown): value is Weather {
  return isRecord(value) && value.source === 'open_meteo' && typeof value.status === 'string'
    && Array.isArray(value.items)
}

function readTravelResult<T>(tools: ToolRun[] | undefined, accepts: (data: unknown) => data is T): T | undefined {
  for (const tool of tools?.slice().reverse() ?? []) {
    const result = tool.result
    if (tool.status === 'completed' && isRecord(result) && result.success === true && accepts(result.data)) {
      return result.data
    }
  }
}

const sourceLabels: Record<string, string> = {
  flyai: '飞猪 FlyAI', ctrip: '携程', baidu: '百度地图', open_meteo: 'Open-Meteo', user: '用户提供',
}
const providerStatuses: Record<string, string> = {
  ok: '已取得数据', partial: '部分数据可用', no_results: '暂无候选', no_data: '暂无数据',
  unconfigured: '待配置', unauthorized: '权限待开通', unavailable: '暂不可用',
  error: '查询未成功', skipped: '本次未查询', out_of_range: '超出预报范围', needs_selection: '地点待确认',
}
const priceBases: Record<string, string> = {
  person: '每人', ticket: '每张', night: '每间每晚', room: '每间',
  total: '总价', stay: '整段住宿',
}
const transportModes: Record<string, string> = { flight: '航班', rail: '火车' }
const arrivalLabels: Record<string, string> = {
  met: '满足到场要求', late: '超过最晚到场时间', unconfirmed: '到场时间待核对',
}
const budgetLabels: Record<string, string> = {
  within: '符合预算', exceeded: '超出预算', unconfirmed: '预算待核对',
}

const textFor = (labels: Record<string, string>, key?: string | null) =>
  key && Object.hasOwn(labels, key) ? labels[key] : undefined

const sourceName = (source: string) => textFor(sourceLabels, source) ?? source
const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const queryTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

function weatherAppearance(code?: number | null) {
  if (code === 0 || code === 1) return { Icon: SunOutlined, label: code === 0 ? '晴' : '大部晴朗', tone: 'sun' }
  if (code === 2 || code === 3) return { Icon: CloudOutlined, label: code === 2 ? '多云' : '阴', tone: 'cloud' }
  if (code === 45 || code === 48) return { Icon: CloudOutlined, label: '雾', tone: 'cloud' }
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code ?? -1)) {
    return { Icon: CloudDownloadOutlined, label: '降雨', tone: 'wet' }
  }
  if ([71, 73, 75, 77, 85, 86].includes(code ?? -1)) return { Icon: CloudDownloadOutlined, label: '降雪', tone: 'wet' }
  if ([95, 96, 99].includes(code ?? -1)) return { Icon: ThunderboltOutlined, label: '雷暴', tone: 'wet' }
  return { Icon: CloudOutlined, label: undefined, tone: 'cloud' }
}

function formatAmount(amount?: Amount | null): string | undefined {
  if (amount == null || (typeof amount === 'string' && amount.trim() === '')) return undefined
  const value = Number(amount)
  return Number.isFinite(value) && value >= 0 ? numberFormat.format(value) : undefined
}

function formatPrice(amount?: Amount | null, currency?: string | null): string | undefined {
  const value = formatAmount(amount)
  if (!value || !currency) return undefined
  return currency === 'CNY' ? `${value} 元` : `${currency} ${value}`
}

const displayTime = (value: string, defaultZone?: string) => value.replace(
  /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/,
  (_, day, clock, zone) => [day, clock, zone === 'Z' ? 'UTC' : zone ?? defaultZone].filter(Boolean).join(' '),
)

function supplierLink(value?: string | null): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? value : undefined
  } catch {
    return undefined
  }
}

function offerQuote(offer?: Offer | null) {
  if (!offer) return undefined
  const value = formatPrice(offer.amount, offer.currency) ?? formatAmount(offer.amount)
  if (!value) return undefined
  const basis = textFor(priceBases, offer.price_basis)
  const missing = [!offer.currency && '币种', !basis && '计价单位'].filter(Boolean).join('、')
  const terms = [basis, missing && `${missing}待确认`].filter(Boolean).join(' · ')
  return { value, terms, label: offer.full_price && offer.currency ? '完整报价' : '参考价' }
}

const resolveOffer = (reference: Offer | string | null | undefined, offers: Map<string, Offer>) =>
  typeof reference === 'string' ? offers.get(reference) : reference

const costSummary = (plan: TravelPlan) => formatPrice(plan.known_cost, plan.currency)
  ?? Object.entries(plan.known_costs ?? {}).map(([unit, amount]) => formatPrice(amount, unit)).filter(Boolean).join(' · ')

function OfferDetails({ offer }: { offer: Offer }) {
  const href = supplierLink(offer.booking_url)
  const quote = offerQuote(offer)
  const stations = offer.from_stop || offer.to_stop
    ? `${offer.from_stop ?? '待确认'} → ${offer.to_stop ?? '待确认'}`
    : undefined
  const facts = [textFor(transportModes, offer.mode), stations, offer.address, offer.room_type, offer.fare_class]
    .filter(Boolean).join(' · ')
  const departure = offer.departs_at || offer.departs_local
  const arrival = offer.arrives_at || offer.arrives_local
  const journeys = [
    departure && `出发 ${displayTime(departure, '+08:00')}`,
    arrival && `到达 ${displayTime(arrival, '+08:00')}`,
    offer.check_in && `入住 ${offer.check_in}`,
    offer.check_out && `退房 ${offer.check_out}`,
  ].filter(Boolean)
  return (
    <>
      <strong>{offer.name}</strong>
      {facts && <span>{facts}</span>}
      {journeys.map(time => <span key={time}>{time}</span>)}
      <small>
        {sourceName(offer.source)} · {quote
          ? `${quote.label} ${quote.value}${quote.terms ? `（${quote.terms}）` : ''}`
          : '价格待确认'}
      </small>
      {offer.queried_at && <small>查询于 <time dateTime={offer.queried_at}>{displayTime(offer.queried_at)}</time></small>}
      {href && <a href={href} aria-label={`查看 ${offer.name} 的平台详情`} target="_blank" rel="noopener noreferrer">
        平台详情 <ExportOutlined aria-hidden="true" />
      </a>}
    </>
  )
}

function PlanCard({ plan, number, offers, selected, sameDay, hasGround, onChoose, onCosts }: {
  plan: TravelPlan
  number: number
  offers: Map<string, Offer>
  selected: boolean
  sameDay: boolean
  hasGround: boolean
  onChoose?: (number: number) => void
  onCosts: () => void
}) {
  const outbound = resolveOffer(plan.transport, offers)
  const inbound = resolveOffer(plan.return_transport, offers)
  const hotel = resolveOffer(plan.hotel, offers)
  const PlanIcon = outbound?.mode === 'flight' ? SendOutlined : outbound?.mode === 'rail' ? CarOutlined : CompassOutlined
  const cost = costSummary(plan)
  const arrival = textFor(arrivalLabels, plan.arrival_status)
  const budget = textFor(budgetLabels, plan.budget_status)
  const notes = [...new Set([...(plan.missing ?? []), ...(plan.warnings ?? [])])]
  const legs = [['去程', plan.transport], ['返程', plan.return_transport], ['住宿', plan.hotel]] as const
  const hotelRequired = plan.cost_items ? Object.hasOwn(plan.cost_items, 'hotel') : !sameDay
  const numberedLabel = `方案 ${number}`
  const travelMode = outbound?.mode === 'flight' ? '航班出行' : outbound?.mode === 'rail' ? '铁路出行' : undefined
  const complete = !!cost && plan.complete_price && !hasGround
  const hasQuote = legs.some(([, ref]) => !!offerQuote(resolveOffer(ref, offers)))
  const feeLabel = complete ? '可核算总费用' : cost ? '已知费用小计' : hasQuote ? '仅有平台参考价' : '费用待报价'
  const departure = outbound?.departs_at ?? outbound?.departs_local
  const arrivalTime = outbound?.arrives_at ?? outbound?.arrives_local
  return (
    <li className="travel-plan">
      <div className="travel-plan-header">
        <div className="travel-plan-title">
          <span className="travel-receipt-icon"><PlanIcon aria-hidden="true" /></span>
          <div>{plan.label !== numberedLabel && <small>{numberedLabel}</small>}
            <h4>{plan.label === numberedLabel && travelMode ? `${numberedLabel} · ${travelMode}` : plan.label}</h4>
          </div>
        </div>
      </div>
      <div className="travel-cost" data-complete={!!complete}><small>{feeLabel}</small>
        <strong>{cost || '暂无可核算小计'}</strong></div>
      <div className="travel-plan-tags">
        {arrival && <ResultTag icon="◷" tone={plan.arrival_status === 'met' ? 'positive' : 'warning'}>{arrival}</ResultTag>}
        {budget && <ResultTag icon="¥" tone={plan.budget_status === 'within' ? 'positive' : 'warning'}>{budget}</ResultTag>}
      </div>
      <div className="travel-plan-route">
        <strong>{[outbound?.from_stop, outbound?.to_stop].filter(Boolean).join(' → ') || outbound?.name || '去程待确认'}</strong>
        {outbound && <span>去程 · {outbound.name}</span>}
        {departure && <small>出发 {displayTime(departure, '+08:00')}</small>}
        {arrivalTime && <small>抵达 {displayTime(arrivalTime, '+08:00')}</small>}
        {inbound && <span>返程 · {inbound.name}</span>}
        {hotel && <span>住宿 · {hotel.name}</span>}
        {plan.arrival_at_venue && <small>预计 {displayTime(plan.arrival_at_venue)} 到场（含预留时间）</small>}
      </div>
      <div className="travel-plan-actions">
        <button type="button" className="travel-fee-link" onClick={onCosts}>查看费用明细</button>
        {!selected && onChoose && <button type="button" className="travel-choose" onClick={() => onChoose(number)}>
          选择方案 {number} <ArrowRightOutlined aria-hidden="true" />
        </button>}
        {selected && <ResultTag tone="positive" icon={<CheckCircleOutlined />}>已保存为行程草稿</ResultTag>}
      </div>
      <details className="travel-plan-breakdown"><summary>交通与住宿</summary><dl className="travel-legs">
        {legs.map(([label, reference]) => {
          const offer = resolveOffer(reference, offers)
          const LegIcon = label === '住宿' ? BankOutlined : offer?.mode === 'flight' ? SendOutlined : CarOutlined
          const placeholder = label === '住宿' && !hotelRequired ? '无需住宿' : '待确认'
          return <div key={label}><dt><LegIcon aria-hidden="true" />{label}</dt><dd>{offer ? <OfferDetails offer={offer} /> : placeholder}</dd></div>
        })}
      </dl>
      <div className="travel-plan-tags">
        {plan.arrival_commute_minutes != null && <ResultTag icon={<CompassOutlined />}>抵达后到会场约 {plan.arrival_commute_minutes} 分钟</ResultTag>}
        {plan.hotel_commute_minutes != null && <ResultTag icon={<CompassOutlined />}>酒店到会场约 {plan.hotel_commute_minutes} 分钟</ResultTag>}
      </div>
      </details>
      {notes.length > 0 && <details className="travel-plan-checks">
        <summary>待核对事项（{notes.length}）</summary>
        <ul className="travel-issues">{notes.map(note => <li key={note}>{note}</li>)}</ul>
      </details>}
    </li>
  )
}

function TravelCosts({ plans, offers, request, selectedId, onSelect }: {
  plans: TravelPlan[]
  offers: Map<string, Offer>
  request: TravelData['request']
  selectedId?: string | null
  onSelect: (id: string) => void
}) {
  const plan = plans.find(item => item.id === selectedId) ?? plans[0]
  if (!plan) return <p className="travel-empty">暂未取得可核算的报价。</p>

  const subtotal = costSummary(plan)
  const rows = [
    { kind: 'outbound', label: '去程交通', offer: resolveOffer(plan.transport, offers) },
    { kind: 'return', label: '返程交通', offer: resolveOffer(plan.return_transport, offers) },
    { kind: 'hotel', label: '住宿', offer: resolveOffer(plan.hotel, offers) },
    { kind: 'ground', label: '会场接驳', offer: undefined },
  ] as const
  const visibleRows = rows.filter(({ kind, offer }) => {
    if (plan.cost_items) return Object.hasOwn(plan.cost_items, kind)
    if (kind === 'ground') return !!request.venue
    return kind !== 'hotel' || !!offer || request.depart_on !== request.return_on
  })

  return (
    <div className="travel-cost-panel">
      {plans.length > 1 && <Segmented aria-label="选择费用方案" size="small" value={plan.id}
        options={plans.map((item, index) => ({ value: item.id, label: `方案 ${index + 1}` }))}
        onChange={onSelect} />}
      <div className="travel-cost-overview">
        <h4>{plan.label}</h4>
        <div>
          {subtotal && <small>可核算小计</small>}
          <strong>{subtotal || '暂无可核算小计'}</strong>
        </div>
      </div>
      <dl className="travel-fee-list">
        {visibleRows.map(({ kind, label, offer }) => {
          const fee = plan.cost_items?.[kind]
          const amount = formatPrice(fee?.amount, fee?.currency)
          const quote = offerQuote(offer)
          const excluded = fee?.status === 'price_unconfirmed' || fee?.status === 'stay_unconfirmed'
            || plan.missing?.includes(`${label}的可核算报价`)
          const staysUnconfirmed = fee?.status === 'stay_unconfirmed'
            || (kind === 'hotel' && plan.missing?.includes('与行程一致的住宿日期'))
          const notes = [
            amount && quote && amount !== quote.value && `${quote.label} ${quote.value}`,
            quote?.terms,
            staysUnconfirmed && '住宿日期待核对',
            kind === 'hotel' && offer?.price_basis === 'night' && request.rooms == null && '房间数待确认',
            (kind === 'outbound' || kind === 'return') && offer?.price_basis === 'person'
              && request.travelers == null && '人数待确认',
            fee?.status === 'partial' && '完整费用待核对',
            quote && excluded && '未计入小计',
          ].filter(Boolean)
          return <div key={kind}>
            <dt>{label}{offer && <small>{offer.name}</small>}</dt>
            <dd>
              <strong>{amount ?? (quote ? `${quote.label} ${quote.value}`
                : kind === 'ground' || offer ? '待报价' : '暂无候选')}</strong>
              {notes.length > 0 && <small>{notes.join(' · ')}</small>}
            </dd>
          </div>
        })}
      </dl>
    </div>
  )
}

function CandidateStop({ label, station, time }: {
  label: string
  station?: string | null
  time?: string | null
}) {
  const formatted = time ? displayTime(time, '+08:00') : undefined
  const match = formatted?.match(/^\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2})(?: (.*))?$/)
  const dateLabel = match ? `${Number(match[1])}/${Number(match[2])}` : undefined
  const zone = match?.[4] === '+08:00' ? undefined : match?.[4]
  return (
    <div className="travel-offer-stop">
      <small>{label}{dateLabel && ` · ${dateLabel}`}{zone && ` ${zone}`}</small>
      <strong>{match?.[3] ?? formatted ?? '时间待确认'}</strong>
      <span>{station || '站点待确认'}</span>
    </div>
  )
}

function CandidateOffer({ offer, hotel }: { offer: Offer; hotel: boolean }) {
  const thumbnail = hotel ? 'hotel' : offer.mode === 'flight' || offer.mode === 'rail' ? offer.mode : undefined
  const href = supplierLink(offer.booking_url)
  const quote = offerQuote(offer)
  const attributes = hotel
    ? offer.room_type
    : [textFor(transportModes, offer.mode), offer.fare_class].filter(Boolean).join(' · ')
  const queried = offer.queried_at ? new Date(offer.queried_at) : undefined
  return (
    <li className="travel-offer-row">
      <div className="travel-offer-thumbnail">
        {thumbnail ? <>
          <img src={`${import.meta.env.BASE_URL}workbench-travel-${thumbnail}.webp`} alt=""
            width={72} height={72} loading="lazy" decoding="async" />
          {hotel && <small>示意图</small>}
        </> : <CompassOutlined aria-hidden="true" />}
      </div>
      <div className="travel-offer-main">
        <div className="travel-offer-title">
          <strong>{offer.name}</strong>{attributes && <small>{attributes}</small>}
        </div>
        {hotel ? (
          <div className="travel-offer-stay">
            {offer.address && <span>{offer.address}</span>}
            {(offer.check_in || offer.check_out) && <small>
              {[offer.check_in && `入住 ${offer.check_in}`, offer.check_out && `退房 ${offer.check_out}`].filter(Boolean).join(' · ')}
            </small>}
          </div>
        ) : (
          <div className="travel-offer-stops">
            <CandidateStop label="出发" station={offer.from_stop} time={offer.departs_at ?? offer.departs_local} />
            <ArrowRightOutlined aria-hidden="true" />
            <CandidateStop label="到达" station={offer.to_stop} time={offer.arrives_at ?? offer.arrives_local} />
          </div>
        )}
        <small className="travel-offer-source">
          {sourceName(offer.source)}
          {queried && Number.isFinite(queried.getTime()) && <> · <time dateTime={offer.queried_at}>
            {queryTimeFormat.format(queried)} 查询
          </time></>}
        </small>
      </div>
      <div className="travel-offer-price">
        {quote ? <>
          <small>{quote.label}</small><strong>{quote.value}</strong>
          {quote.terms && <small>{quote.terms}</small>}
        </> : <small>价格待确认</small>}
        {href && <a href={href} aria-label={`查看 ${offer.name} 的平台详情`}
          target="_blank" rel="noopener noreferrer">平台详情 <ExportOutlined aria-hidden="true" /></a>}
      </div>
    </li>
  )
}

function TravelCandidates({ offers }: { offers: Offer[] }) {
  const groups = [
    ['outbound', '去程交通'],
    ['return', '返程交通'],
    ['hotel', '住宿候选'],
    ['transport', '交通候选'],
  ] as const
  return <div className="travel-candidate-groups">
    {groups.map(([kind, title]) => {
      const matches = offers.filter(offer => {
        const direction = offer.direction
          ?? (offer.kind === 'hotel' || offer.check_in || offer.check_out ? 'hotel' : 'transport')
        return direction === kind
      })
      if (!matches.length) return null
      return <section aria-label={title} key={kind}>
        <h4>{title}</h4>
        <ul className="travel-offers">{matches.map(offer => (
          <CandidateOffer key={offer.id} offer={offer} hotel={kind === 'hotel'} />
        ))}</ul>
      </section>
    })}
  </div>
}

function WeatherReceipt({ weather }: { weather: Weather }) {
  const location = weather.location ?? weather.requested_city
  const title = weather.scope === 'near_term' ? '近期天气参考' : '出行天气'
  return (
    <section className="travel-weather" aria-label={title}>
      <h4><CloudOutlined aria-hidden="true" />{title}{location && ` · ${location}`}</h4>
      {weather.items?.length ? <ul className="travel-forecast">
        {weather.items.map(day => {
          const { Icon, label, tone } = weatherAppearance(day.weather_code)
          const range = [day.temperature_min_c, day.temperature_max_c].filter(value => value != null).join(' 至 ')
          const observations = [
            day.precipitation_probability_max_pct != null && `${day.precipitation_probability_max_pct}% 降水概率`,
            day.wind_speed_max_kmh != null && `最大风速 ${day.wind_speed_max_kmh} km/h`,
          ].filter(Boolean)
          return (
            <li key={day.date}>
              <span className={`travel-weather-icon travel-weather-${tone}`}><Icon aria-hidden="true" /></span>
              <div><strong>{range ? `${range} °C` : day.date}</strong><small>{range ? day.date : ''}{label && ` · ${label}`}</small></div>
              {!!observations.length && <span>{observations.join(' · ')}</span>}
            </li>
          )
        })}
      </ul> : null}
      {weather.message ? <p>{weather.message}</p> : !weather.items?.length && <p>暂未取得行程日期的天气预报。</p>}
      {!!weather.advice?.length && (
        <div className="travel-weather-advice">
          <strong>天气提醒</strong>
          <ul aria-label="天气提醒">{weather.advice.map(tip => <li key={tip}>{tip}</li>)}</ul>
        </div>
      )}
      <small>
        {weather.source === 'open_meteo'
          ? <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a>
          : sourceName(weather.source)}
        {weather.queried_at && <> · 查询于 <time dateTime={weather.queried_at}>{displayTime(weather.queried_at)}</time></>}
      </small>
    </section>
  )
}

function TravelRoutes({ routes, offers, venue }: {
  routes?: Record<string, TravelRoute>
  offers: Map<string, Offer>
  venue?: string | null
}) {
  const choices = [...new Map(Object.entries(routes ?? {}).map(([id, route]) => [
    JSON.stringify([route.origin ?? offers.get(id)?.name ?? id, route.destination ?? venue]), [id, route] as const,
  ])).values()]
  const [selectedId, setSelectedId] = useState('')
  const selected = choices.find(([id]) => id === selectedId)
    ?? choices.find(([, route]) => route.status === 'ok')
    ?? choices[0]
  const route = selected?.[1]
  const href = supplierLink(route?.source_url)
  if (!selected || !route) return <p className="travel-empty">补齐会场和到达地点后，可查看接驳路线。</p>

  const origin = route.origin ?? offers.get(selected[0])?.name
  const destination = route.destination ?? venue
  const path = route.items?.[0]
  const title = [origin, destination].filter(Boolean).join(' → ') || '会场接驳'
  const options = choices.map(([id, choice]) => ({
    value: id,
    label: choice.origin ?? offers.get(id)?.name ?? '会场接驳',
    note: choice.status === 'needs_selection' ? '地址待确认'
      : choice.status !== 'ok' ? '暂无路线' : undefined,
  }))
  return (
    <div className="travel-route-panel">
      {options.length > 1 && <div className="travel-route-picker">
        <span>接驳路线</span>
        <Select
          className="travel-route-select"
          aria-label="接驳路线"
          prefix={<EnvironmentOutlined aria-hidden="true" />}
          value={selected[0]}
          onChange={setSelectedId}
          options={options}
          virtual={false}
          optionRender={({ data }) => <span className="travel-route-option">
            <span>{data.label}</span>
            {data.note && <span className="travel-route-option-note">{data.note}</span>}
          </span>}
        />
      </div>}
      <div className="travel-route-summary">
        <div>
          <h4><EnvironmentOutlined aria-hidden="true" /> {title}</h4>
          <p>百度地图驾车估算</p>
        </div>
        {(path?.duration_minutes != null || path?.distance_meters != null) && (
          <dl className="travel-route-metrics">
            {path?.duration_minutes != null && <div><dt>预计耗时</dt><dd>{path.duration_minutes} 分钟</dd></div>}
            {path?.distance_meters != null && <div><dt>驾车距离</dt><dd>{numberFormat.format(path.distance_meters / 1_000)} km</dd></div>}
          </dl>
        )}
      </div>
      <BaiduRouteMap key={`${selected[0]}:${route.queried_at ?? ''}`} geometry={route.map}
        sourceUrl={href} label={`${title} · 百度地图`} />
      <div className="travel-route-footer">
        <p className="travel-empty">{route.message ?? (route.status === 'ok' ? '实际耗时以出行时路况为准。' : '等待核对路线数据。')}</p>
        {href && <a className="travel-map-link" href={href} target="_blank" rel="noopener noreferrer">
          在百度地图查看路线 <ExportOutlined aria-hidden="true" />
        </a>}
      </div>
    </div>
  )
}

export function TravelReceipt({ tools, onChoose }: { tools?: ToolRun[], onChoose?: (number: number) => void }) {
  const [tab, setTab] = useState('compare')
  const [viewedPlanId, setViewedPlanId] = useState<string>()
  const data = readTravelResult(tools, isTravelData)
  const weather = data?.weather ?? readTravelResult(tools, isWeather)
  if (!data) return weather
    ? <div className="travel-receipt travel-weather-only"><WeatherReceipt weather={weather} /></div>
    : null
  const { request, plans } = data
  const offers = data.offers ?? []
  const offersById = new Map(offers.map(offer => [offer.id, offer]))
  const plannedOffers = new Set(plans.flatMap(plan => [plan.transport, plan.return_transport, plan.hotel]
    .map(offer => typeof offer === 'string' ? offer : offer?.id)))
  const otherOffers = offers.filter(offer => !plannedOffers.has(offer.id))
  const budget = formatPrice(request.budget_cny, 'CNY')
  const route = [request.origin, request.destination].filter(Boolean).join(' → ')
  const dates = [request.depart_on, request.return_on].filter(Boolean).join(' 至 ')
  const constraints = [
    request.venue,
    request.arrive_by && `${displayTime(request.arrive_by)} 前到场`,
    request.travelers && `${request.travelers} 人`,
    request.rooms && `${request.rooms} 间房`,
    budget && `预算 ${budget}`,
    request.buffer_minutes != null && `预留 ${request.buffer_minutes} 分钟`,
  ].filter(Boolean).join(' · ')
  const chosen = data.selected_plan_id
  const viewedPlan = plans.find(plan => plan.id === (viewedPlanId ?? chosen)) ?? plans[0]
  const outbound = resolveOffer(viewedPlan?.transport, offersById)
  const inbound = resolveOffer(viewedPlan?.return_transport, offersById)
  const lodging = resolveOffer(viewedPlan?.hotel, offersById)
  const schedule = [
    { name: '去程出发', time: outbound?.departs_at || outbound?.departs_local || request.depart_on,
      place: outbound ? [outbound.name, outbound.from_stop].filter(Boolean).join(' · ') : route, icon: CalendarOutlined },
    { name: '抵达目的地', time: outbound?.arrives_at || outbound?.arrives_local, place: outbound?.to_stop, icon: EnvironmentOutlined },
    { name: '入住', time: lodging?.check_in, place: lodging?.name, icon: BankOutlined },
    { name: '预计到场', time: viewedPlan?.arrival_at_venue, place: request.venue, icon: EnvironmentOutlined },
    { name: '最晚到场', time: request.arrive_by, place: request.venue, icon: EnvironmentOutlined },
    { name: '退房', time: lodging?.check_out, place: lodging?.name, icon: BankOutlined },
    { name: '返程出发', time: inbound?.departs_at || inbound?.departs_local || request.return_on,
      place: inbound ? [inbound.name, inbound.from_stop].filter(Boolean).join(' · ') : request.destination, icon: CalendarOutlined },
    { name: '返程抵达', time: inbound?.arrives_at || inbound?.arrives_local, place: inbound?.to_stop, icon: EnvironmentOutlined },
  ].filter(item => item.time)
  const planContent = plans.length ? <ol className="travel-plans">
    {plans.map((plan, index) => <PlanCard key={plan.id} plan={plan} number={index + 1}
      offers={offersById} selected={plan.id === chosen} sameDay={!!request.depart_on && request.depart_on === request.return_on}
      hasGround={!!request.venue}
      onCosts={() => { setViewedPlanId(plan.id); setTab('costs') }}
      onChoose={chosen ? undefined : onChoose} />)}
  </ol> : <p className="travel-empty">现有数据还无法组合出差方案。请查看待核对事项和来源。</p>
  return (
    <section className="travel-receipt" aria-label="出差方案比较">
      <header className="travel-receipt-header">
        <span className="travel-receipt-icon"><CompassOutlined aria-hidden="true" /></span>
        <div>
          <h3>{route || '出差方案'}</h3>
          {dates && <p>{dates}</p>}
          {constraints && <p>{constraints}</p>}
        </div>
        <img className="travel-receipt-art" src={`${import.meta.env.BASE_URL}workbench-travel.webp`} alt="" width={128} height={64} />
      </header>
      <Tabs className="travel-tabs" size="small" tabBarGutter={14} activeKey={tab} onChange={setTab} items={[
        { key: 'compare', label: '方案对比', children: (
          <div className="travel-comparison">
            {planContent}
            {!!data.issues?.length && <ul className="travel-issues travel-receipt-issues">{data.issues.map((issue, index) => (
              <li key={`${issue}-${index}`}>{issue}</li>
            ))}</ul>}
            {!!(otherOffers.length || data.providers?.length) && (
              <details className="travel-sources" open={plans.length === 0 || undefined}>
                <summary>候选与来源</summary>
                {otherOffers.length > 0 && <TravelCandidates offers={otherOffers} />}
                {!!data.providers?.length && <ul className="travel-providers">{data.providers.map((provider, index) => (
                  <li key={`${provider.source}-${index}`}>
                    <strong>{provider.label ?? sourceName(provider.source)}</strong> · {textFor(providerStatuses, provider.status) ?? '状态待核对'}
                    {provider.message && <span> · {provider.message}</span>}
                    {provider.queried_at && <small>查询于 <time dateTime={provider.queried_at}>{displayTime(provider.queried_at)}</time></small>}
                  </li>
                ))}</ul>}
              </details>
            )}
            {plans.length > 0 && onChoose && !chosen && <p className="travel-next-step">可以回复方案序号，也可以补充或修改会议时间。发送确认后会保存行程草稿。</p>}
          </div>
        ) },
        { key: 'schedule', label: '行程安排', children: (
          <div className="travel-schedule">
            {plans.length > 1 && <Segmented size="small" aria-label="选择行程方案" value={viewedPlan.id}
              options={plans.map((plan, index) => ({ value: plan.id, label: `方案 ${index + 1}` }))} onChange={setViewedPlanId} />}
            {schedule.length ? <ol className="travel-tab-records">{schedule.map(({ name, time, place, icon: Icon }) => (
              <li key={name}><strong><Icon aria-hidden="true" /> {name}</strong><time dateTime={time!}>{displayTime(time!, '+08:00')}</time>
                {place && <span>{place}</span>}</li>
            ))}</ol> : <p className="travel-empty">等待补充出发、到场或返程时间。</p>}
          </div>
        ) },
        { key: 'routes', label: '地图与路线', children: <TravelRoutes routes={data.routes} offers={offersById} venue={request.venue} /> },
        ...(weather ? [{ key: 'weather', label: '天气参考', children: <WeatherReceipt weather={weather} /> }] : []),
        { key: 'costs', label: '费用明细', children: <TravelCosts plans={plans} offers={offersById}
          request={request} selectedId={viewedPlan?.id} onSelect={setViewedPlanId} /> },
      ]} />

    </section>
  )
}
