import { DatabaseOutlined } from '@ant-design/icons'

import type { ToolRun } from '../types/agent'
import { ToolResultView } from './ToolResultView'
import {
  isPresentationBase,
  isSeoSearchOverviewResultData,
  resolveLocalizedText,
  unwrapToolResult,
} from './renderers/contracts'

function formatLatency(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1)}s`
  }
  return `${Math.round(ms)}ms`
}

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

  return (
    <section className="inline-tool-results" aria-label="数据证据">
      <header className="inline-tool-results-title">
        <span className="inline-tool-results-icon" aria-hidden="true"><DatabaseOutlined /></span>
        <div>
          <strong>数据证据</strong>
          <span>页面事实和查询结果保留来源；优化建议是待人工确认的草稿。</span>
        </div>
      </header>
      <div className="inline-tool-results-list">
        {visible.map((tool) => {
          const presentation = unwrapToolResult(tool.result)
          const selfContained = isSeoSearchOverviewResultData(presentation)
          const title = isPresentationBase(presentation)
            ? resolveLocalizedText(presentation.title, '查询结果')
            : '查询结果'
          const description = isPresentationBase(presentation)
            ? resolveLocalizedText(presentation.description, '本次调查获得的结构化数据。')
            : '本次调查获得的结构化数据。'
          return (
            <article className={`inline-tool-result-card${selfContained ? ' self-contained' : ''}`} key={tool.id}>
              {!selfContained ? (
                <header>
                  <div>
                    <h3>{title}</h3>
                    <p>{description}</p>
                  </div>
                  <span>
                    已获取{tool.latencyMs != null ? ` · ${formatLatency(tool.latencyMs)}` : ''}
                  </span>
                </header>
              ) : null}
              <ToolResultView hint={tool.render_hint} data={tool.result} mode="chat" />
            </article>
          )
        })}
      </div>
    </section>
  )
}
