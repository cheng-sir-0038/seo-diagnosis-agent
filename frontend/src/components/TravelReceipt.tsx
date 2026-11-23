import { ArrowRightOutlined, CheckCircleOutlined, CompassOutlined, ExportOutlined } from '@ant-design/icons'

import type { ToolRun } from '../types/agent'
import { ResultTag } from './ResultTag'
import './TravelReceipt.css'

type Amount = number | string

interface Offer {
  id: string
  name: string
  source: string
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
    temperature_min_c?: number | null
    temperature_max_c?: number | null
    precipitation_probability_max_pct?: number | null
    wind_speed_max_kmh?: number | null
  }[]
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

const displayTime = (value: string) => value.replace(
  /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/,
  (_, day, clock, zone) => [day, clock, zone === 'Z' ? 'UTC' : zone].filter(Boolean).join(' '),
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

function OfferDetails({ offer }: { offer: Offer }) {
  const href = supplierLink(offer.booking_url)
  const price = formatPrice(offer.amount, offer.currency) ?? formatAmount(offer.amount)
  const priceTerms = [
    !offer.currency && '币种待确认',
    textFor(priceBases, offer.price_basis) ?? '计价单位待确认',
  ].filter(Boolean).join('、')
  const stations = offer.from_stop || offer.to_stop
    ? `${offer.from_stop ?? '待确认'} → ${offer.to_stop ?? '待确认'}`
    : undefined
  const facts = [textFor(transportModes, offer.mode), stations, offer.address, offer.room_type, offer.fare_class]
    .filter(Boolean).join(' · ')
  const journeys = [
    offer.departs_at ? `出发 ${displayTime(offer.departs_at)}`
      : offer.departs_local && `出发 ${offer.departs_local}（时区待确认）`,
    offer.arrives_at ? `到达 ${displayTime(offer.arrives_at)}`
      : offer.arrives_local && `到达 ${offer.arrives_local}（时区待确认）`,
    offer.check_in && `入住 ${offer.check_in}`,
    offer.check_out && `退房 ${offer.check_out}`,
  ].filter(Boolean)
  return (
    <>
      <strong>{offer.name}</strong>
      {facts && <span>{facts}</span>}
      {journeys.map(time => <span key={time}>{time}</span>)}
      <small>
        {sourceName(offer.source)} · {price
          ? `${offer.full_price && offer.currency ? '完整报价' : '参考价'} ${price}（${priceTerms}）`
          : '价格待确认'}
      </small>
      {offer.queried_at && <small>查询于 <time dateTime={offer.queried_at}>{displayTime(offer.queried_at)}</time></small>}
      {href && <a href={href} aria-label={`查看 ${offer.name} 的平台详情`} target="_blank" rel="noopener noreferrer">
        平台详情 <ExportOutlined aria-hidden="true" />
      </a>}
    </>
  )
}

function PlanCard({ plan, number, offers, selected, sameDay, onChoose }: {
  plan: TravelPlan
  number: number
  offers: Map<string, Offer>
  selected: boolean
  sameDay: boolean
  onChoose?: (number: number) => void
}) {
  const cost = formatPrice(plan.known_cost, plan.currency)
    ?? Object.entries(plan.known_costs ?? {}).map(([currency, amount]) => formatPrice(amount, currency)).filter(Boolean).join(' · ')
  const arrival = textFor(arrivalLabels, plan.arrival_status)
  const budget = textFor(budgetLabels, plan.budget_status)
  const notes = [...new Set([...(plan.missing ?? []), ...(plan.warnings ?? [])])]
  const legs = [['去程', plan.transport], ['返程', plan.return_transport], ['住宿', plan.hotel]] as const
  const numberedLabel = `方案 ${number}`
  return (
    <li className="travel-plan">
      <div className="travel-plan-header">
        <div>{plan.label !== numberedLabel && <small>{numberedLabel}</small>}<h4>{plan.label}</h4></div>
        <div className="travel-cost"><small>{plan.complete_price ? '方案总价' : '已知费用'}</small><strong>{cost || '待确认'}</strong></div>
      </div>
      <dl className="travel-legs">
        {legs.map(([label, reference]) => {
          const offer = typeof reference === 'string' ? offers.get(reference) : reference
          const placeholder = label === '住宿' && sameDay ? '当天往返' : '待确认'
          return <div key={label}><dt>{label}</dt><dd>{offer ? <OfferDetails offer={offer} /> : placeholder}</dd></div>
        })}
      </dl>
      <div className="travel-plan-tags">
        {plan.arrival_commute_minutes != null && <ResultTag icon={<CompassOutlined />}>抵达后到会场约 {plan.arrival_commute_minutes} 分钟</ResultTag>}
        {plan.hotel_commute_minutes != null && <ResultTag icon={<CompassOutlined />}>酒店到会场约 {plan.hotel_commute_minutes} 分钟</ResultTag>}
        {arrival && <ResultTag icon="◷" tone={plan.arrival_status === 'met' ? 'positive' : 'warning'}>{arrival}</ResultTag>}
        {budget && <ResultTag icon="¥" tone={plan.budget_status === 'within' ? 'positive' : 'warning'}>{budget}</ResultTag>}
      </div>
      {plan.arrival_at_venue && <small>预计 {displayTime(plan.arrival_at_venue)} 到场（含预留时间）</small>}
      {notes.length > 0 && <ul className="travel-issues">{notes.map(note => <li key={note}>{note}</li>)}</ul>}
      {selected && <ResultTag tone="positive" icon={<CheckCircleOutlined />}>已保存为行程草稿</ResultTag>}
      {!selected && onChoose && <button type="button" className="travel-choose" onClick={() => onChoose(number)}>
        选择方案 {number} <ArrowRightOutlined aria-hidden="true" />
      </button>}
    </li>
  )
}

function WeatherReceipt({ weather }: { weather: Weather }) {
  const location = [weather.location ?? weather.requested_city, weather.timezone].filter(Boolean).join(' · ')
  const title = weather.scope === 'near_term' ? '近期天气参考' : '出行天气'
  return (
    <section className="travel-weather" aria-label={title}>
      <h4>{title}{location && ` · ${location}`}</h4>
      {weather.items?.length ? <ul>
        {weather.items.map(day => {
          const range = [day.temperature_min_c, day.temperature_max_c].filter(value => value != null).join(' 至 ')
          const observations = [
            range && `${range} °C`,
            day.precipitation_probability_max_pct != null && `${day.precipitation_probability_max_pct}% 降水概率`,
            day.wind_speed_max_kmh != null && `最大风速 ${day.wind_speed_max_kmh} km/h`,
          ].filter(Boolean)
          return <li key={day.date}><strong>{day.date}</strong><span>{observations.join(' · ') || '天气指标待确认'}</span></li>
        })}
      </ul> : null}
      {weather.message ? <p>{weather.message}</p> : !weather.items?.length && <p>暂未取得行程日期的天气预报。</p>}
      {!!weather.advice?.length && (
        <div className="travel-weather-advice">
          <strong>出行建议</strong>
          <ul aria-label="出行建议">{weather.advice.map(tip => <li key={tip}>{tip}</li>)}</ul>
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

export function TravelReceipt({ tools, onChoose }: { tools?: ToolRun[], onChoose?: (number: number) => void }) {
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
  return (
    <section className="travel-receipt" aria-label="出差方案比较">
      <header className="travel-receipt-header">
        <span className="travel-receipt-icon"><CompassOutlined aria-hidden="true" /></span>
        <div>
          <h3>{route || '出差方案'}</h3>
          {dates && <p>{dates}</p>}
          {constraints && <p>{constraints}</p>}
        </div>
      </header>
      {plans.length ? <ol className="travel-plans">
        {plans.map((plan, index) => <PlanCard key={plan.id} plan={plan} number={index + 1}
          offers={offersById} selected={plan.id === chosen} sameDay={!!request.depart_on && request.depart_on === request.return_on}
          onChoose={chosen ? undefined : onChoose} />)}
      </ol> : <p className="travel-empty">现有数据还无法组合出差方案。请查看待核对事项和来源。</p>}
      {!!data.issues?.length && <ul className="travel-issues travel-receipt-issues">{data.issues.map((issue, index) => (
        <li key={`${issue}-${index}`}>{issue}</li>
      ))}</ul>}
      {weather && <WeatherReceipt weather={weather} />}
      {!!(otherOffers.length || data.providers?.length) && (
        <details className="travel-sources" open={plans.length === 0 || undefined}>
          <summary>候选与来源</summary>
          {!!data.providers?.length && <ul className="travel-providers">{data.providers.map((provider, index) => (
            <li key={`${provider.source}-${index}`}>
              <strong>{provider.label ?? sourceName(provider.source)}</strong> · {textFor(providerStatuses, provider.status) ?? '状态待核对'}
              {provider.message && <span> · {provider.message}</span>}
              {provider.queried_at && <small>查询于 <time dateTime={provider.queried_at}>{displayTime(provider.queried_at)}</time></small>}
            </li>
          ))}</ul>}
          {!!otherOffers.length && <ul className="travel-offers">{otherOffers.map(offer => (
            <li key={offer.id}><OfferDetails offer={offer} /></li>
          ))}</ul>}
        </details>
      )}
      {plans.length > 0 && onChoose && !chosen && <p className="travel-next-step">可以回复方案序号，也可以补充或修改会议时间。发送确认后会保存行程草稿。</p>}
    </section>
  )
}
