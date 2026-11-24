import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { type ReactNode, useEffect, useMemo, useState } from 'react'

interface RawJsonResultProps {
  data: unknown
}

function highlightJson(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let offset = 0
  const tokens = /("(?:\\.|[^"\\])*")(\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g
  for (const token of text.matchAll(tokens)) {
    const start = token.index
    if (start > offset) nodes.push(text.slice(offset, start))
    const tone = token[2] ? 'key' : token[1] ? 'string' : /^[\d-]/.test(token[0]) ? 'number' : 'literal'
    nodes.push(<span key={start} className={`json-token-${tone}`}>{token[1] ?? token[0]}</span>)
    if (token[2]) nodes.push(token[2])
    offset = start + token[0].length
  }
  if (offset < text.length) nodes.push(text.slice(offset))
  return nodes
}

export function RawJsonResult({ data }: RawJsonResultProps) {
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle')
  const text = useMemo(() => JSON.stringify(data, null, 2) ?? '', [data])
  const highlighted = useMemo(() => text.length <= 160_000 ? highlightJson(text) : text, [text])
  const copyLabel = copyStatus === 'copied' ? '已复制' : copyStatus === 'failed' ? '重试复制' : '复制'

  useEffect(() => {
    if (copyStatus === 'idle') return
    const timer = window.setTimeout(() => setCopyStatus('idle'), 2_000)
    return () => window.clearTimeout(timer)
  }, [copyStatus])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopyStatus('copied')
    } catch {
      setCopyStatus('failed')
    }
  }

  return (
    <div className="json-viewer" role="region" aria-label="格式化内容">
      <div className="json-viewer-actions">
        <span className="json-copy-feedback" role="status">
          {copyStatus === 'copied' ? '内容已复制' : copyStatus === 'failed' ? '未能复制，可选中内容手动复制' : ''}
        </span>
        <button className="json-copy-button" type="button" onClick={() => void copy()}
          aria-label={copyStatus === 'idle' ? '复制内容' : undefined} data-status={copyStatus}>
          {copyStatus === 'copied' ? <CheckOutlined aria-hidden="true" /> : <CopyOutlined aria-hidden="true" />}
          <span>{copyLabel}</span>
        </button>
      </div>
      <pre className="tool-result-raw" data-testid="raw-tool-result" tabIndex={0}><code>{highlighted}</code></pre>
    </div>
  )
}
