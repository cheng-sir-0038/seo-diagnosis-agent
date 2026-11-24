import {
  ApartmentOutlined, CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, LinkOutlined,
  LoadingOutlined, RightOutlined, RobotOutlined, StopOutlined,
} from '@ant-design/icons'
import { Tabs } from 'antd'
import { createElement, useState } from 'react'

import { specialists, specialistStatusLabels } from '../specialists'
import type { AppliedSkill, RunStatus, SpecialistRun, ToolRun } from '../types/agent'
import type { DocumentSummary } from '../types/workspace'
import { DocumentChips } from './ChatAttachments'
import { toolIcon, toolResultLabel } from './toolLabels'
import { unwrapToolResult } from './renderers/contracts'
import { RawJsonResult } from './renderers/RawJsonResult'

const toolStatus = { completed: '已完成', failed: '失败', running: '执行中', cancelled: '已停止' }

function ToolRunDetails({ tool }: { tool: ToolRun }) {
  const [open, setOpen] = useState(false)
  const [resultOpen, setResultOpen] = useState(false)
  const hasDetails = tool.arguments !== undefined || tool.result !== undefined || Boolean(tool.error)
  const milliseconds = tool.latencyMs
  const duration = milliseconds == null ? null
    : milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)} 秒`
      : `${Math.round(milliseconds)} 毫秒`
  const heading = (
    <>
      <span className="chat-run-tool-title"><span className="workspace-row-icon">
        {createElement(toolIcon(tool.name), { 'aria-hidden': true })}
      </span>
        <strong>{toolResultLabel(tool)}</strong></span>
      <span className="chat-run-tool-meta">
        {tool.status === 'running' && <LoadingOutlined spin aria-hidden="true" />}
        <span className={`chat-run-tool-status ${tool.status}`}>{toolStatus[tool.status]}</span>
        {duration ? <span>{duration}</span> : null}
      </span>
    </>
  )
  if (!hasDetails) return <div className="chat-run-tool"><div className="chat-run-tool-heading">{heading}</div></div>

  return (
    <details className="chat-run-tool" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary className="chat-run-tool-heading">{heading}<RightOutlined aria-hidden="true" /></summary>
      {open && (
        <div className="chat-run-tool-body">
          {tool.arguments !== undefined && <section aria-label="调用参数">
            <h4>调用参数</h4>
            <RawJsonResult data={tool.arguments} />
          </section>}
          {tool.error && <p className="chat-run-tool-error">{tool.error}</p>}
          {tool.result !== undefined && (
            <details className="chat-run-result" open={resultOpen} onToggle={event => setResultOpen(event.currentTarget.open)}>
              <summary><RightOutlined aria-hidden="true" /> 返回数据</summary>
              {resultOpen && <RawJsonResult data={tool.result} />}
            </details>
          )}
        </div>
      )}
    </details>
  )
}

export function ChatRunDetails({ tools, agentRuns = [], title = '执行详情', initiallyOpen = false }: {
  tools?: ToolRun[]
  agentRuns?: SpecialistRun[]
  title?: string
  initiallyOpen?: boolean
}) {
  const [open, setOpen] = useState(initiallyOpen)
  if (!tools?.length) return null
  const groups = agentRuns.map(run => ({
    id: run.id,
    name: specialists[run.agent].name,
    tools: tools.filter(tool => tool.agentRunId === run.id),
  })).filter(group => group.tools.length)
  const unassigned = tools.filter(tool => !groups.some(group => group.id === tool.agentRunId))

  return (
    <details className="chat-run-details" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <RightOutlined aria-hidden="true" />
        <span>{title}</span>
        <span className="chat-run-count">{tools.length} 次调用</span>
      </summary>
      {open ? (
        <div className="chat-run-calls">
          {groups.map(group => (
            <section className="chat-run-group" key={group.id} aria-label={group.name}>
              <h3>{group.name}</h3>
              {group.tools.map(tool => <ToolRunDetails key={tool.id} tool={tool} />)}
            </section>
          ))}
          {unassigned.length > 0 && (
            <div className="chat-run-group">
              {groups.length > 0 && <h3>其他工具</h3>}
              {unassigned.map(tool => <ToolRunDetails key={tool.id} tool={tool} />)}
            </div>
          )}
        </div>
      ) : null}
    </details>
  )
}

const isEvidenceRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

function runSources(tools: ToolRun[]) {
  const evidence = new Map<string, string>()
  for (const tool of tools) {
    const result = unwrapToolResult(tool.result)
    if (tool.status !== 'completed' || !isEvidenceRecord(result)) continue
    const sources = [
      result, result.provenance, result.weather,
      ...(isEvidenceRecord(result.routes) ? Object.values(result.routes) : []),
      ...(Array.isArray(result.sources) ? result.sources : []),
      ...(Array.isArray(result.references) ? result.references : []),
      ...(Array.isArray(result.offers) ? result.offers : []),
      ...(Array.isArray(result.quotes) ? result.quotes : []),
    ]
    for (const source of sources) {
      const record = isEvidenceRecord(source) ? source : undefined
      const raw = typeof source === 'string' ? source : record?.source_url ?? record?.url ?? record?.booking_url
        ?? (record?.source === 'open_meteo' && record.status === 'ok' ? 'https://open-meteo.com/' : undefined)
      if (typeof raw !== 'string') continue
      let url: URL
      try {
        url = new URL(raw)
      } catch {
        continue
      }
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) continue
      const label = record?.title ?? record?.source_label ?? record?.label ?? record?.name ?? record?.supplier
      evidence.set(url.href, typeof label === 'string' && label ? label : url.hostname)
    }
  }
  return [...evidence].map(([url, label]) => ({ url, label }))
}

export function ExecutionTimeline({ stage, agentRuns = [], skills = [], tools = [], started, running, elapsedMs, totalTokens,
  documents = [], onDownload, status, partial }: {
  stage?: string
  agentRuns?: SpecialistRun[]
  skills?: AppliedSkill[]
  tools?: ToolRun[]
  started: boolean
  running?: boolean
  status?: RunStatus
  partial?: boolean
  elapsedMs?: number | null
  totalTokens?: number | null
  documents?: DocumentSummary[]
  onDownload: (document: DocumentSummary) => void
}) {
  if (!started) return <p className="workbench-empty">收到执行事件后，这里会显示参与的 Agent 和工具。</p>
  const sources = runSources(tools)
  const workingTool = tools.find(tool => tool.status === 'running')
  const waiting = status === 'waiting_for_input' || partial
  const SummaryIcon = running ? LoadingOutlined : status === 'completed' ? CheckCircleOutlined
    : status === 'failed' ? ExclamationCircleOutlined : status === 'cancelled' ? StopOutlined : ClockCircleOutlined
  const tone = running ? 'running' : waiting ? 'waiting' : status === 'failed' ? 'failed'
    : status === 'completed' ? 'completed' : 'neutral'
  const elapsed = elapsedMs == null ? '—' : elapsedMs >= 1_000 ? `${(elapsedMs / 1_000).toFixed(2)} 秒` : `${Math.round(elapsedMs)} 毫秒`
  const flow = (
    <>
      <div className="execution-summary" data-tone={tone}>
        <SummaryIcon spin={running} aria-hidden="true" />
        <strong>{workingTool ? `正在调用 ${toolResultLabel(workingTool)}` : stage ?? '执行详情'}</strong>
      </div>
      <h3 className="execution-section-title"><ApartmentOutlined aria-hidden="true" />Agent 协作链路</h3>
      <ol className="workspace-execution-list" aria-label="本轮执行链路">
        <li>
          <div className="workspace-execution-step">
            <span className="workspace-row-icon"><RobotOutlined aria-hidden="true" /></span>
            <strong>主 Agent</strong>
          </div>
        </li>
        {agentRuns.map(agentRun => {
          const { name, icon: Icon, tone } = specialists[agentRun.agent]
          const calls = tools.filter(tool => tool.agentRunId === agentRun.id).length
          const skill = skills.find(item => item.agentRunId === agentRun.id)?.label
          return (
            <li key={agentRun.id}>
              <div className={`workspace-execution-step specialist-${tone}`}>
                <span className="workspace-row-icon"><Icon aria-hidden="true" /></span>
                <strong>{name}</strong>
                <span className="participant-status" data-status={agentRun.status}>{specialistStatusLabels[agentRun.status]}</span>
                {(skill || calls > 0) && <small>{[skill, calls > 0 ? `${calls} 次工具调用` : null].filter(Boolean).join(' · ')}</small>}
              </div>
            </li>
          )
        })}
      </ol>
      <ChatRunDetails title="工具调用详情" tools={tools} agentRuns={agentRuns} initiallyOpen />
      <dl className="execution-stats" aria-label="运行统计">
        <div><dt>总耗时</dt><dd>{elapsed}</dd></div>
        <div><dt>工具调用</dt><dd>{tools.length} 次</dd></div>
        <div><dt>已记录 Token</dt><dd>{totalTokens == null ? '—' : totalTokens.toLocaleString('zh-CN')}</dd></div>
      </dl>
    </>
  )
  return (
    <Tabs className="execution-tabs" size="small" tabBarGutter={12} items={[
      { key: 'flow', label: '执行过程', children: flow },
      { key: 'sources', label: sources.length ? `引用来源 ${sources.length}` : '引用来源', children: sources.length ? (
        <ul className="execution-sources">{sources.map(source => <li key={source.url}>
          <a href={source.url} target="_blank" rel="noopener noreferrer"><LinkOutlined aria-hidden="true" />{source.label}</a>
        </li>)}</ul>
      ) : <p className="workbench-empty">本轮暂无可打开的引用来源。</p> },
      { key: 'documents', label: documents.length ? `相关文件 ${documents.length}` : '相关文件', children: documents.length
        ? <DocumentChips documents={documents} onDownload={onDownload} />
        : <p className="workbench-empty">本轮暂无相关附件。</p> },
    ]} />
  )
}
