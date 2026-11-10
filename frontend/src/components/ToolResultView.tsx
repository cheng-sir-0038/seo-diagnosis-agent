import { lazy, Suspense } from 'react'

import type { RenderHint } from '../types/agent'
import { CardResult } from './renderers/CardResult'
import { RawJsonResult } from './renderers/RawJsonResult'
import { TableResult } from './renderers/TableResult'
import { TreeResult } from './renderers/TreeResult'
import { isCardResultData, isChartResultData, isPageMetadataData, isPagePerformanceData, isSeoSearchOverviewResultData, isTableResultData, isTreeResultData, unwrapToolResult } from './renderers/contracts'

const LazyChartResult = lazy(async () => {
  const module = await import('./renderers/ChartResult')
  return { default: module.ChartResult }
})

interface ToolResultViewProps {
  hint?: RenderHint
  data: unknown
  mode?: 'chat' | 'inspector'
}

export function ToolResultView({ hint, data, mode = 'inspector' }: ToolResultViewProps) {
  const rendererData = unwrapToolResult(data)
  if (hint === 'chart' && (isPageMetadataData(rendererData) || isPagePerformanceData(rendererData) || isSeoSearchOverviewResultData(rendererData) || isChartResultData(rendererData))) {
    return (
      <Suspense fallback={<div className="tool-result-loading">正在加载图表…</div>}>
        <LazyChartResult data={rendererData} rawData={data} />
      </Suspense>
    )
  }
  if (hint === 'table' && isTableResultData(rendererData)) {
    return <TableResult data={rendererData} rawData={data} />
  }
  if (hint === 'tree' && isTreeResultData(rendererData)) {
    return <TreeResult data={rendererData} rawData={data} />
  }
  if (hint === 'cards' && isCardResultData(rendererData)) {
    return <CardResult data={rendererData} />
  }
  if (mode === 'chat') {
    return <div className="tool-result-unavailable">该结果暂时无法以图表展示，可在“执行详情”中查看原始数据。</div>
  }
  return <RawJsonResult data={data} />
}
