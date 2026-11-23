import { RightOutlined } from '@ant-design/icons'
import { useState } from 'react'

import { specialistNames, type ToolRun } from '../types/agent'
import { toolResultLabel } from './toolLabels'
import { RawJsonResult } from './renderers/RawJsonResult'

const toolStatus = { completed: '已完成', failed: '失败', running: '执行中' }

function ToolRunDetails({ tool }: { tool: ToolRun }) {
  const [open, setOpen] = useState(false)
  const args = tool.arguments
  const parameters = args && typeof args === 'object' && !Array.isArray(args) ? Object.entries(args) : []
  const milliseconds = tool.latencyMs
  const duration = milliseconds == null ? null
    : milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)} 秒`
      : `${Math.round(milliseconds)} 毫秒`

  return (
    <div className="chat-run-tool">
      <div className="chat-run-tool-heading">
        <strong>{toolResultLabel(tool)}</strong>
        <div className="chat-run-tool-meta">
          {tool.agent ? <span>{specialistNames[tool.agent]}</span> : null}
          <span className={`chat-run-tool-status ${tool.status}`}>{toolStatus[tool.status]}</span>
          {duration ? <span>{duration}</span> : null}
        </div>
      </div>
      {parameters.length ? (
        <dl className="chat-run-parameters" aria-label="调用参数">
          {parameters.map(([key, value]) => (
            <div key={key}>
              <dt><code>{key}</code></dt>
              <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {tool.error ? <p className="chat-run-tool-error">{tool.error}</p> : null}
      {tool.result !== undefined ? (
        <details className="chat-run-result" onToggle={(event) => setOpen(event.currentTarget.open)}>
          <summary><RightOutlined aria-hidden="true" /> 返回数据</summary>
          {open ? <RawJsonResult data={tool.result} /> : null}
        </details>
      ) : null}
    </div>
  )
}

export function ChatRunDetails({ tools }: { tools?: ToolRun[] }) {
  const [open, setOpen] = useState(false)
  if (!tools?.length) return null

  return (
    <details className="chat-run-details" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <RightOutlined aria-hidden="true" />
        <span>执行详情</span>
        <span className="chat-run-count">{tools.length} 次调用</span>
      </summary>
      {open ? (
        <div className="chat-run-calls">
          {tools.map((tool) => <ToolRunDetails key={tool.id} tool={tool} />)}
        </div>
      ) : null}
    </details>
  )
}
