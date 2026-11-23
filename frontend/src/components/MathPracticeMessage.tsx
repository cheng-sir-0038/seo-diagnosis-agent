import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import 'katex/dist/katex.css'

import { MarkdownMessage, type MarkdownMessageProps } from './MarkdownMessage'

export default function MathPracticeMessage({ content, results }: MarkdownMessageProps) {
  return (
    <MarkdownMessage
      content={content}
      results={results}
      remarkPlugins={[remarkMath]}
      rehypePlugins={[rehypeKatex]}
    />
  )
}
