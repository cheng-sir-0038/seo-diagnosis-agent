import { RightOutlined } from '@ant-design/icons'
import { Alert, Button } from 'antd'
import { useEffect, useState } from 'react'

import { listWorkspaceTools } from '../services/agentApi'
import { specialists, type Specialist } from '../specialists'
import type { SpecialistDirectoryData } from '../types/agent'
import { PageHeader } from './PageHeader'
import { toolDescription, toolIcon, toolLabel } from './toolLabels'

const specialistTones = {
  healthcare: 'health',
  seo: 'seo',
  learning: 'learning',
  exam_prep: 'learning',
  followup: 'health',
  travel: 'travel',
  shopping_procurement: 'procurement',
} satisfies Record<Specialist, string>

export default function SpecialistDirectory({ conversationId }: { conversationId: string }) {
  const [directory, setDirectory] = useState<SpecialistDirectoryData>()
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [activeAgent, setActiveAgent] = useState<Specialist>('healthcare')

  useEffect(() => {
    const request = new AbortController()
    listWorkspaceTools(conversationId, request.signal).then(result => {
      if (!request.signal.aborted) setDirectory(result)
    }).catch(() => {
      if (!request.signal.aborted) setError(true)
    })
    return () => request.abort()
  }, [conversationId, attempt])

  const selected = directory?.specialists.find(({ agent }) => agent === activeAgent) ?? directory?.specialists[0]

  return (
    <div className="page-content workspace-resources">
      <PageHeader title="Agent 能力" description="选择专员，查看可用的工具与技能。" />
      {error && (
        <Alert type="error" showIcon title="暂时无法读取工具目录。"
          action={<Button size="small" aria-label="重试" onClick={() => {
            setError(false)
            setAttempt(value => value + 1)
          }}>重试</Button>} />
      )}
      {!error && !directory && <p className="workbench-empty" role="status">正在读取工具目录…</p>}
      {!error && directory && (selected ? (
        <>
          <div className="workbench-domains workspace-specialist-choices" role="group" aria-label="专员">
            {directory.specialists.map(({ agent, tools }) => {
              const { name, icon: Icon } = specialists[agent]
              return (
                <button type="button" key={agent} className={`workbench-domain workbench-tone-${specialistTones[agent]}`}
                  aria-pressed={agent === selected.agent} aria-controls="workspace-specialist-tools"
                  onClick={() => setActiveAgent(agent)}>
                  <span className="workbench-domain-title"><Icon aria-hidden="true" /><strong>{name}</strong></span>
                  <span className="workbench-domain-description">{tools.length} 个工具<RightOutlined aria-hidden="true" /></span>
                </button>
              )
            })}
          </div>
          <section id="workspace-specialist-tools"
            className={`workbench-panel workspace-specialist-tools workbench-tone-${specialistTones[selected.agent]}`}
            aria-labelledby="workspace-specialist-title">
            <h2 id="workspace-specialist-title">{specialists[selected.agent].name}</h2>
            {selected.note && <p className="workspace-resource-hint">{selected.note}</p>}
            {selected.tools.length ? (
              <ul className="workspace-specialist-tool-list">
                {selected.tools.map(tool => {
                  const skill = selected.skills?.find(item => item.tools.includes(tool.name))
                  const Icon = toolIcon(tool.name)
                  return (
                    <li key={tool.name}>
                      <h3><span className="workspace-row-icon"><Icon aria-hidden="true" /></span>{toolLabel(tool.name)}</h3>
                      <p>{toolDescription(tool.name, tool.description)}</p>
                      {skill && <small>Skill · {skill.label}</small>}
                      <code>{tool.name}</code>
                    </li>
                  )
                })}
              </ul>
            ) : <p className="workbench-empty">当前没有可用工具。</p>}
          </section>
        </>
      ) : <p className="workbench-empty">暂无可展示的专员。</p>)}
    </div>
  )
}
