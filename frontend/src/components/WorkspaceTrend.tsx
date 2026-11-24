import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export default function WorkspaceTrend({ days }: { days: { day: string, count: number }[] }) {
  return (
    <figure aria-label={days.map(({ day, count }) => `${day} ${count} 个任务`).join('，')}>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={days} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
          <XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: '#615f76' }} minTickGap={12} />
          <YAxis allowDecimals={false} width={24} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: '#615f76' }} />
          <Tooltip />
          <Area type="monotone" dataKey="count" name="任务数" stroke="#6452ef" strokeWidth={2} fill="#e9e5ff" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </figure>
  )
}
