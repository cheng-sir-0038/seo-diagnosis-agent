import { specialists, specialistStatusLabels } from '../specialists'
import type { AppliedSkill, SpecialistRun, ToolRun } from '../types/agent'
import { toolLabel } from './toolLabels'

interface Props {
  agentRuns?: SpecialistRun[]
  skills?: AppliedSkill[]
  tools?: ToolRun[]
}

export function AssistantRunStatus({ agentRuns, skills, tools }: Props) {
  return (
    <>
      <div className="assistant-name">业务助理</div>
      {!!agentRuns?.length && (
        <ol className="assistant-agents" aria-label="专员进度">
          {agentRuns.map(run => {
            const { name, icon: Icon, tone } = specialists[run.agent]
            const actions = [...new Set(tools?.filter(tool => tool.agentRunId === run.id).map(tool => toolLabel(tool.name)))]
            const skill = skills?.find(item => item.agentRunId === run.id)
            return (
              <li key={run.id} className={`specialist-${tone}`}>
                <div className="participant-heading">
                  <span className="participant-label"><Icon aria-hidden="true" />{name}</span>
                  <span className="participant-status" data-status={run.status}>{specialistStatusLabels[run.status]}</span>
                </div>
                {skill && <span className="participant-skill">Skill · {skill.label}</span>}
                {actions.length > 0 && <small>{actions.slice(0, 2).join(' · ')}{actions.length > 2 ? ` 等 ${actions.length} 项` : ''}</small>}
              </li>
            )
          })}
        </ol>
      )}
    </>
  )
}
