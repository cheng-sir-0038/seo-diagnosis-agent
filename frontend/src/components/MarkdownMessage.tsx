import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { MarkdownDataSummary } from './MarkdownDataSummary'
import { sanitizeAssistantContent } from './assistantContentSanitizer'
import { splitMarkdownContent } from './markdownDataSummaryParser'

export function MarkdownMessage({ content }: { content: string }) {
  const sanitized = sanitizeAssistantContent(content)
  const segments = splitMarkdownContent(sanitized.content)
  return (
    <div className="markdown-body">
      {segments.map((segment, index) => (
        segment.type === 'data-summary'
          ? <MarkdownDataSummary data={segment.data} key={`summary-${index}`} />
          : (
              <ReactMarkdown remarkPlugins={[remarkGfm]} key={`markdown-${index}`}>
                {segment.content}
              </ReactMarkdown>
            )
      ))}
    </div>
  )
}
