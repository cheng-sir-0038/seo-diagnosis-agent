import { Tree } from 'antd'
import type { DataNode } from 'antd/es/tree'

import { RawJsonResult } from './RawJsonResult'

import { isTreeResultData } from './contracts'

export function TreeResult({ data, rawData = data }: { data: unknown; rawData?: unknown }) {
  if (!isTreeResultData(data)) return <RawJsonResult data={rawData} />
  const treeData = data.nodes as DataNode[]
  return (
    <div className="tool-tree-result" data-testid="tree-result">
      <Tree
        showLine
        selectable={false}
        treeData={treeData}
        defaultExpandedKeys={data.nodes.slice(0, 3).map((node) => node.key)}
      />
    </div>
  )
}
