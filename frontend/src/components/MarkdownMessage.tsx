import type { Root } from 'hast'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { MarkdownDataSummary } from './MarkdownDataSummary'
import { sanitizeAssistantContent } from './assistantContentSanitizer'
import { splitMarkdownContent } from './markdownDataSummaryParser'

const resultSlotClass = 'assistant-result-slot'

function insertResultSlot() {
  return (tree: Root) => {
    const lead = tree.children.findIndex((node) => node.type === 'element' && node.tagName === 'p')
    tree.children.splice(lead < 0 ? tree.children.length : lead + 1, 0, {
      type: 'element', tagName: 'div', properties: { className: [resultSlotClass] }, children: [],
    })
  }
}

export function MarkdownMessage({ content, results }: { content: string; results?: ReactNode }) {
  const sanitized = sanitizeAssistantContent(content)
  const segments = splitMarkdownContent(sanitized.content)
  const lead = results ? segments.findIndex((segment) => segment.type === 'markdown') : -1
  return (
    <div className="markdown-body">
      {segments.map((segment, index) => (
        segment.type === 'data-summary'
          ? <MarkdownDataSummary data={segment.data} key={`summary-${index}`} />
          : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={index === lead ? [insertResultSlot] : undefined}
                components={index === lead ? {
                  div: ({ node, children, ...props }) =>
                    node?.properties.className?.toString() === resultSlotClass
                      ? results
                      : <div {...props}>{children}</div>,
                } : undefined}
                key={`markdown-${index}`}
              >
                {segment.content}
              </ReactMarkdown>
            )
      ))}
      {lead < 0 ? results : null}
    </div>
  )
}
