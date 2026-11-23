import { Drawer, Tabs } from 'antd'
import { useEffect, useState } from 'react'

import { listWorkspaceTools } from '../services/agentApi'
import { specialists } from '../specialists'
import type { SpecialistDirectoryData } from '../types/agent'
import { toolDescription, toolLabel } from './toolLabels'

export default function SpecialistDirectory({ conversationId, onClose }: { conversationId: string, onClose: () => void }) {
  const [directory, setDirectory] = useState<SpecialistDirectoryData>()
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const request = new AbortController()
    listWorkspaceTools(conversationId, request.signal).then(result => {
      if (!request.signal.aborted) setDirectory(result)
    }).catch(() => {
      if (!request.signal.aborted) setError(true)
    })
    return () => request.abort()
  }, [conversationId, attempt])

  return (
    <Drawer title="专员与工具" open onClose={onClose} size="default">
      {error ? <div role="alert">暂时无法读取工具目录。 <button type="button" onClick={() => { setError(false); setAttempt(attempt + 1) }}>重试</button></div>
        : !directory ? <p role="status">正在读取工具目录…</p>
          : <Tabs defaultActiveKey="healthcare" items={directory.specialists.map(({ agent, tools, skills, note }) => {
            const { name, icon: Icon } = specialists[agent]
            return { key: agent,
              label: <span className="directory-tab"><Icon aria-hidden="true" />{name}<small aria-label={`${tools.length} 个工具`}>{tools.length}</small></span>,
              children: <section className="directory-group" aria-label={`${name}工具`}>
                {note && <p>{note}</p>}
                <ul>{tools.map(tool => {
                  const skill = skills?.find(item => item.tools.includes(tool.name))
                  return (
                    <li key={tool.name}>
                      <strong>{toolLabel(tool.name)}</strong>
                      <p>{toolDescription(tool.name, tool.description)}</p>
                      {skill && <p className="directory-skill">Skill · {skill.label}</p>}
                      <code>{tool.name}</code>
                    </li>
                  )
                })}</ul>
              </section>,
            }
          })} />}
    </Drawer>
  )
}
