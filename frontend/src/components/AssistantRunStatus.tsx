import { LoadingOutlined, RightOutlined } from '@ant-design/icons'

import { specialistPresentation, specialistStatusLabels } from '../specialists'
import type { SpecialistRun } from '../types/agent'

export function AssistantRunStatus({ agentRuns, stage, running, onInspect }: {
  agentRuns?: SpecialistRun[]
  stage?: string
  running?: boolean
  onInspect?: () => void
}) {
  return (
    <div className="assistant-collaboration">
      <span className="assistant-name">主 Agent</span>
      {!!agentRuns?.length && <ol aria-label="专员进度">
        {agentRuns.map(run => {
          const { name, icon: Icon } = specialistPresentation(run.agent, run.profile)
          return <li key={run.id}><RightOutlined aria-hidden="true" /><Icon aria-hidden="true" />{name}
            <small data-status={run.status}>{specialistStatusLabels[run.status]}</small>
          </li>
        })}
      </ol>}
      {running && <span role="status" aria-label="分析进度"><LoadingOutlined spin /> {stage}</span>}
      {onInspect && <button type="button" onClick={onInspect}>本轮执行详情<RightOutlined aria-hidden="true" /></button>}
    </div>
  )
}
