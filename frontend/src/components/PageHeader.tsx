import type { ReactElement, ReactNode } from 'react'

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }): ReactElement {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  )
}
