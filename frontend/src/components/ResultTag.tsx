import type { ReactNode } from 'react'

export type ResultTagTone = 'neutral' | 'source' | 'positive' | 'warning' | 'danger' | 'accent'

export function ResultTag({ icon, tone = 'neutral', children }: {
  icon: ReactNode
  tone?: ResultTagTone
  children: ReactNode
}) {
  return (
    <span className={`result-tag is-${tone}`}>
      <span className="result-tag-icon" aria-hidden="true">{icon}</span>
      <span className="result-tag-label">{children}</span>
    </span>
  )
}
