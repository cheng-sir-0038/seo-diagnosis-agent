import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { PageMetadataResult } from './PageMetadataResult'
import { PagePerformanceResult } from './PagePerformanceResult'
import { RawJsonResult } from './RawJsonResult'
import { isChartResultData, isPageMetadataData, isPagePerformanceData, isSeoSearchOverviewResultData } from './contracts'
import { SeoSearchOverview } from './SeoSearchOverview'

const palette = ['#2563eb', '#059669', '#d97706', '#7c3aed', '#dc2626', '#0891b2']

export function ChartResult({ data, rawData = data }: { data: unknown; rawData?: unknown }) {
  if (isPageMetadataData(data)) return <PageMetadataResult data={data} />
  if (isPagePerformanceData(data)) return <PagePerformanceResult data={data} />
  if (isSeoSearchOverviewResultData(data)) return <SeoSearchOverview data={data} />
  if (!isChartResultData(data)) return <RawJsonResult data={rawData} />

  if (data.type === 'pie') {
    const series = data.datasets[0]
    const pieData = data.labels.map((label, index) => ({ name: label, value: series.data[index] }))
    return (
      <div className="tool-chart-result" data-testid="chart-result">
        <ResponsiveContainer width="100%" height="100%" minWidth={280} minHeight={230}>
          <PieChart>
            <Tooltip />
            <Legend />
            <Pie data={pieData} dataKey="value" nameKey="name" innerRadius="35%" outerRadius="70%" isAnimationActive={false}>
              {pieData.map((item, index) => <Cell key={`${item.name}-${index}`} fill={palette[index % palette.length]} />)}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
    )
  }

  const rows = data.labels.map((label, index) => {
    const row: Record<string, string | number> = { label }
    data.datasets.forEach((dataset, datasetIndex) => {
      row[`series_${datasetIndex}`] = dataset.data[index]
    })
    return row
  })

  if (data.type === 'bar') {
    return (
      <div className="tool-chart-result" data-testid="chart-result">
        <ResponsiveContainer width="100%" height="100%" minWidth={280} minHeight={230}>
          <BarChart data={rows} margin={{ top: 12, right: 8, left: -18, bottom: 12 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip />
            <Legend />
            {data.datasets.map((dataset, index) => (
              <Bar key={dataset.label} dataKey={`series_${index}`} name={dataset.label} fill={palette[index % palette.length]} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  return (
    <div className="tool-chart-result" data-testid="chart-result">
      <ResponsiveContainer width="100%" height="100%" minWidth={280} minHeight={230}>
        <LineChart data={rows} margin={{ top: 12, right: 8, left: -18, bottom: 12 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip />
          <Legend />
          {data.datasets.map((dataset, index) => (
            <Line
              key={dataset.label}
              dataKey={`series_${index}`}
              name={dataset.label}
              stroke={palette[index % palette.length]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
