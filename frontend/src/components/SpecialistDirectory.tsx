import { FileTextOutlined, RightOutlined } from '@ant-design/icons'
import { Alert, Button } from 'antd'
import { createElement, useEffect, useState } from 'react'

import { listWorkspaceTools } from '../services/agentApi'
import bundledSkillCatalog from '../services/bundledSkillCatalog.json'
import { specialistPresentation, type Specialist } from '../specialists'
import type { DirectorySkillUsage, SpecialistDirectoryData } from '../types/agent'
import { PageHeader } from './PageHeader'
import { toolDescription, toolIcon, toolLabel } from './toolLabels'

type DirectorySpecialist = SpecialistDirectoryData['specialists'][number]
type DirectoryPrompt = NonNullable<DirectorySpecialist['default_prompts']>[number]
type DirectoryTool = DirectorySpecialist['tools'][number]
type SkillDetails = { name: string; label: string; summary: string; instruction?: string; usage?: DirectorySkillUsage }
type BundledSkill = Omit<SkillDetails, 'usage'> & {
  agent: string
  instruction: string
  tools: string[]
  usage: Partial<Record<string, DirectorySkillUsage>>
}

const builtInSkills: BundledSkill[] = bundledSkillCatalog.skills
const usageFields = {
  scenario: '适用场景', inputs: '需要提供', rules: '处理规则', output: '交付结果',
} satisfies Record<keyof DirectorySkillUsage, string>

function readSkillDetails(specialist: DirectorySpecialist, tool: DirectoryTool, promptsById: ReadonlyMap<string, DirectoryPrompt>): SkillDetails[] {
  const bindings = specialist.skills?.filter(skill => skill.tools.includes(tool.name)) ?? []
  if (specialist.skill_catalog_version === 1) {
    return bindings.map(skill => ({
      name: skill.name, label: skill.label, summary: skill.summary ?? '',
      instruction: promptsById.get(skill.name)?.text,
      usage: skill.usage ? skill.usage[tool.name] : bindings.length === 1 ? tool.skill_usage ?? undefined : undefined,
    }))
  }
  if (specialist.skill_catalog_version != null) return []

  // Older payloads lack an explicit publication contract. Use approved bundled text
  // for the exact specialist, tool and declared Skill, when a binding is present.
  const hasBindings = Boolean(specialist.skills?.length) || tool.skill_usage != null
  return builtInSkills.filter(entry => entry.agent === specialist.agent && entry.tools.includes(tool.name)
    && (!hasBindings || bindings.some(skill => skill.name === entry.name)))
    .map(({ name, label, summary, instruction, usage }) => ({
      name, label, summary, instruction, usage: usage[tool.name],
    }))
}

function readPublicPrompts(specialist: DirectorySpecialist, tool: DirectoryTool, promptsById: ReadonlyMap<string, DirectoryPrompt>) {
  if (specialist.skill_catalog_version === 1) {
    const skillIds = new Set(specialist.skills?.map(skill => skill.name))
    return [...new Set(tool.public_prompt_ids)].flatMap(id => {
      const prompt = promptsById.get(id)
      return prompt && !skillIds.has(id) ? [prompt] : []
    })
  }
  if (specialist.skill_catalog_version != null) return []
  const promptIds = tool.public_prompt_ids ?? [...promptsById.keys()]
  return bundledSkillCatalog.instructions.filter(prompt => prompt.agent === specialist.agent
    && promptIds.includes(prompt.id) && (prompt.tools === null || prompt.tools.includes(tool.name)))
}

function SkillInstruction({ label, text, initiallyOpen, note }: {
  label: string
  text: string
  initiallyOpen: boolean
  note?: string
}) {
  return (
    <details className="workspace-skill-instruction" open={initiallyOpen}>
      <summary className="workbench-disclosure workspace-skill-instruction-summary">
        <FileTextOutlined aria-hidden="true" />
        <span>{label}</span>
      </summary>
      <div className="workspace-skill-instruction-body">
        {note && <p>{note}</p>}
        <pre>{text}</pre>
      </div>
    </details>
  )
}

function CapabilityRow({ tool, specialist, skills, promptsById }: {
  specialist: DirectorySpecialist
  tool: DirectoryTool
  skills: SkillDetails[]
  promptsById: ReadonlyMap<string, DirectoryPrompt>
}) {
  const description = toolDescription(tool.name, tool.description)
  const knownLabel = toolLabel(tool.name)
  const label = knownLabel === tool.name ? (description.trim() || knownLabel) : knownLabel
  const statusId = tool.availability_note ? `capability-status-${tool.name}` : undefined
  const publicPrompts = readPublicPrompts(specialist, tool, promptsById)
  const hasSkillDetails = skills.length > 0 || publicPrompts.length > 0
  const canExpand = hasSkillDetails || Boolean(tool.availability_note || tool.guidance || specialist.deterministic_path)

  const row = (
    <>
      <span className="workspace-row-icon">{createElement(toolIcon(tool.name), { 'aria-hidden': true })}</span>
      <span className="workspace-capability-copy">
        <strong>{label}</strong>
        {description !== label && <span>{description}</span>}
      </span>
      {statusId && <span id={statusId} className="workspace-capability-status">实时查询不可用</span>}
    </>
  )

  return (
    <li>
      {canExpand ? (
        <details className="workspace-capability-fold">
          <summary className="workbench-disclosure workspace-capability-summary" aria-label={label} aria-describedby={statusId}>
            {row}
          </summary>
          <div className="workspace-capability-detail">
            {tool.availability_note && <p className="workspace-capability-notice">
              {tool.availability_note} <a href="#/sources">查看数据源</a>
            </p>}
            {tool.guidance && <p>{tool.guidance}</p>}
            {specialist.deterministic_path && <p>{specialist.deterministic_path}</p>}
            {hasSkillDetails && (
              <div className="workspace-specialist-prompt-list" role="region"
                aria-label={skills.length ? `${label}的 Skill 详情` : `${label}的使用指令`}>
                {skills.map(skill => (
                  <div className="workspace-skill-sheet" key={skill.name}>
                    <h3>Skill：{skill.label}</h3>
                    {skill.usage ? (
                      <dl className="workspace-skill-usage">
                        {(Object.keys(usageFields) as (keyof DirectorySkillUsage)[]).map(field => (
                          <div key={field}>
                            <dt>{usageFields[field]}</dt>
                            <dd>{skill.usage?.[field]}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : skill.summary && <p>{skill.summary}</p>}
                    {skill.instruction && (
                      <SkillInstruction label="原始 Skill 指令" text={skill.instruction} initiallyOpen={!skill.usage} />
                    )}
                  </div>
                ))}
                {publicPrompts.map(prompt => (
                  <SkillInstruction key={prompt.id} label={prompt.label} text={prompt.text} initiallyOpen={!skills.length}
                    note={specialist.agent === 'seo' && prompt.id === 'seo-course-editor' ? '仅在结合考纲核对课程页时使用。' : undefined} />
                ))}
              </div>
            )}
          </div>
        </details>
      ) : <div className="workspace-capability-summary">{row}</div>}
    </li>
  )
}

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
  const selectedProfile = specialistPresentation(selected?.agent, selected?.profile)
  const promptsById = new Map(selected?.default_prompts?.filter(prompt => prompt.text.trim()).map(prompt => [prompt.id, prompt]))
  const toolSkills = selected?.tools.map(tool => ({
    tool,
    skills: readSkillDetails(selected, tool, promptsById),
  })) ?? []
  const missingSkillDetails = toolSkills.some(({ tool, skills }) => (
    (selected?.skill_catalog_version === undefined && !skills.length) || selected?.skills?.some(skill => (
      skill.tools.includes(tool.name) && !skills.some(detail => detail.name === skill.name)
    ))
  ))

  function reloadDirectory() {
    if (selected) setActiveAgent(selected.agent)
    setError(false)
    setDirectory(undefined)
    setAttempt(value => value + 1)
  }

  return (
    <div className="page-content workspace-resources">
      <PageHeader title="Agent 能力" description="展开能力，查看对应的 Skill、所需条件和交付结果。" />
      {error && (
        <Alert type="error" showIcon title="暂时无法读取工具目录。"
          action={<Button size="small" aria-label="重试" onClick={reloadDirectory}>重试</Button>} />
      )}
      {!error && !directory && <p className="workbench-empty" role="status">正在读取工具目录…</p>}
      {!error && directory && (selected ? (
        <>
          <div className="workbench-domains workspace-specialist-choices" role="group" aria-label="专员">
            {directory.specialists.map(({ agent, profile, tools }) => {
              const { name, icon: Icon, directoryTone } = specialistPresentation(agent, profile)
              return (
                <button type="button" key={agent} className={`workbench-domain workbench-tone-${directoryTone}`}
                  aria-pressed={agent === selected.agent} aria-controls="workspace-specialist-tools"
                  onClick={() => setActiveAgent(agent)}>
                  <span className="workbench-domain-title"><Icon aria-hidden="true" /><strong>{name}</strong></span>
                  <span className="workbench-domain-description">{tools.length} 项能力<RightOutlined aria-hidden="true" /></span>
                </button>
              )
            })}
          </div>
          <section key={selected.agent} id="workspace-specialist-tools"
            className={`workbench-panel workspace-specialist-tools workbench-tone-${selectedProfile.directoryTone}`}
            aria-labelledby="workspace-specialist-title">
            <h2 id="workspace-specialist-title">{selectedProfile.name}</h2>
            {selected.note && <p className="workspace-resource-hint">{selected.note}</p>}
            {missingSkillDetails && (
              <div className="workspace-capability-version">
                <p className="workspace-resource-hint" role="status">当前工具目录的数据版本未提供完整的 Skill 详情。</p>
                <Button type="link" size="small" onClick={reloadDirectory}>重新加载工具目录</Button>
              </div>
            )}
            {toolSkills.length ? (
              <ul className="workspace-specialist-tool-list">
                {toolSkills.map(({ tool, skills }) => (
                  <CapabilityRow key={tool.name} tool={tool} skills={skills} specialist={selected} promptsById={promptsById} />
                ))}
              </ul>
            ) : <p className="workbench-empty">当前没有已登记工具。</p>}
          </section>
        </>
      ) : <p className="workbench-empty">暂无可展示的专员。</p>)}
    </div>
  )
}
