import { CodeOutlined, SearchOutlined } from '@ant-design/icons'

import type { ToolRun } from '../types/agent'
import { ResultTag } from './ResultTag'
import { ToolResultView } from './ToolResultView'
import {
  isPageMetadataData,
  isPresentationBase,
  isSeoSearchOverviewResultData,
  resolveLocalizedText,
  unwrapToolResult,
} from './renderers/contracts'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeEvidence(value: unknown, path: string[] = []): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeEvidence(item, path))
  }
  if (!isRecord(value)) return value

  const parent = path.at(-1)
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => (
        key !== 'raw'
        && !(key === 'fetched_at' && (parent === 'provenance' || parent === 'meta'))
      ))
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => [key, normalizeEvidence(nested, [...path, key])]),
  )
}

function evidenceFingerprint(tool: ToolRun): string {
  const presentation = unwrapToolResult(tool.result)
  return `${tool.render_hint ?? 'unknown'}:${JSON.stringify(normalizeEvidence(presentation))}`
}

function visibleEvidence(tools?: ToolRun[]): ToolRun[] {
  const seen = new Set<string>()
  return (tools ?? []).filter((tool) => {
    if (tool.status !== 'completed' || tool.result === undefined || !tool.render_hint) return false
    const fingerprint = evidenceFingerprint(tool)
    if (seen.has(fingerprint)) return false
    seen.add(fingerprint)
    return true
  })
}

export function InlineToolResults({ tools }: { tools?: ToolRun[] }) {
  const visible = visibleEvidence(tools)
  if (visible.length === 0) return null
  const onlyShareImages = visible.every(tool => tool.name === 'seo_share_image')

  return (
    <section className="inline-tool-results" aria-label={onlyShareImages ? '生成的分享图' : '本次结果'}>
      {visible.map((tool) => {
        const result = unwrapToolResult(tool.result)
        const selfContained = tool.name === 'seo_share_image'
          || (tool.render_hint === 'chart' && (isPageMetadataData(result) || isSeoSearchOverviewResultData(result)))
        const presentation = isPresentationBase(result) ? result : null
        const description = resolveLocalizedText(presentation?.description, '')
        const provenance = presentation?.provenance
        const source = tool.render_hint === 'cards' || tool.render_hint === 'tree'
          ? provenance?.source_label ?? provenance?.source
          : undefined
        const hasDraft = presentation?.capability === 'page_suggestions'
          && isRecord(result) && isRecord(result.proposal)
        return (
          <article className={`inline-tool-result-card${selfContained ? ' self-contained' : ''}`} key={tool.id}>
            {!selfContained && presentation ? (
              <header>
                <div>
                  <h3>{resolveLocalizedText(presentation.title, '查询结果')}</h3>
                  {description ? <p>{description}</p> : null}
                </div>
                {source || hasDraft ? (
                  <div className="inline-result-tags">
                    {source ? (
                      <ResultTag icon={provenance?.source === 'page_html' ? <CodeOutlined /> : <SearchOutlined />} tone="source">
                        {source}{provenance?.country ? ` · ${provenance.country}` : ''}
                      </ResultTag>
                    ) : null}
                    {source && provenance?.is_estimated ? <ResultTag icon="≈">估算数据</ResultTag> : null}
                    {hasDraft ? <ResultTag icon="✦" tone="accent">建议草稿</ResultTag> : null}
                  </div>
                ) : null}
              </header>
            ) : null}
            <ToolResultView hint={tool.render_hint} data={tool.result} mode="chat" />
          </article>
        )
      })}
    </section>
  )
}
