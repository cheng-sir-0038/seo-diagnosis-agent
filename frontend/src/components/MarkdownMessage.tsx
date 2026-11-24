import type { Root } from 'hast'
import { createContext, type ComponentProps, type ReactNode, useContext } from 'react'
import ReactMarkdown, { type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { MarkdownDataSummary } from './MarkdownDataSummary'
import { sanitizeAssistantContent } from './assistantContentSanitizer'
import { splitMarkdownContent } from './markdownDataSummaryParser'

const resultSlotClass = 'assistant-result-slot'
const ResultSlot = createContext<ReactNode>(null)

function MarkdownDiv({ node, children, ...props }: ComponentProps<'div'> & ExtraProps) {
  const results = useContext(ResultSlot)
  return node?.properties.className?.toString() === resultSlotClass
    ? <>{results}</> : <div {...props}>{children}</div>
}

function insertResultSlot() {
  return (tree: Root) => {
    const lead = tree.children.findIndex((node) => node.type === 'element' && node.tagName === 'p')
    tree.children.splice(lead < 0 ? tree.children.length : lead + 1, 0, {
      type: 'element', tagName: 'div', properties: { className: [resultSlotClass] }, children: [],
    })
  }
}

export interface MarkdownMessageProps {
  content: string
  results?: ReactNode
  remarkPlugins?: NonNullable<ComponentProps<typeof ReactMarkdown>['remarkPlugins']>
  rehypePlugins?: NonNullable<ComponentProps<typeof ReactMarkdown>['rehypePlugins']>
}

export function MarkdownMessage({
  content, results, remarkPlugins = [], rehypePlugins = [],
}: MarkdownMessageProps) {
  const sanitized = sanitizeAssistantContent(content)
  const segments = splitMarkdownContent(sanitized.content)
  const lead = results ? segments.findIndex((segment) => segment.type === 'markdown') : -1
  return (
    <div className="markdown-body"><ResultSlot.Provider value={results}>
      {segments.map((segment, index) => (
        segment.type === 'data-summary'
          ? <MarkdownDataSummary data={segment.data} key={`summary-${index}`} />
          : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm, ...remarkPlugins]}
                rehypePlugins={index === lead ? [...rehypePlugins, insertResultSlot] : rehypePlugins}
                components={{
                  a: ({ node, href, children, ...props }) => {
                    const start = node?.position?.start.offset
                    if (typeof children === 'string' && start != null
                      && /^https?:\/\//i.test(children) && segment.content.startsWith(children, start)) {
                      // GFM bare URLs can consume a Chinese closing delimiter and the prose after it.
                      const end = children.search(/。|）/u)
                      if (end > 0 && !children.slice(0, end).includes('（')) {
                        const url = children.slice(0, end)
                        return <><a {...props} href={url}>{url}</a>{children.slice(end)}</>
                      }
                    }
                    return <a {...props} href={href}>{children}</a>
                  },
                  div: MarkdownDiv,
                }}
                key={`markdown-${index}`}
              >
                {segment.content}
              </ReactMarkdown>
            )
      ))}
      {lead < 0 ? results : null}
    </ResultSlot.Provider></div>
  )
}
