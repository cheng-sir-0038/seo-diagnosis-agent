const statusClass: Record<string, string> = {
  RUNNING: 'status-running',
  ACTIVE: 'status-success',
  READY: 'status-success',
  COMPLETED: 'status-success',
  APPROVED: 'status-success',
  EDITED_AND_APPROVED: 'status-success',
  ACCEPTED: 'status-success',
  EFFECTIVE: 'status-success',
  WAITING_FOR_APPROVAL: 'status-warning',
  WAITING_FOR_DECISION: 'status-warning',
  PENDING: 'status-warning',
  MEDIUM: 'status-warning',
  FAILED: 'status-error',
  REJECTED: 'status-error',
  INEFFECTIVE: 'status-error',
  CANCELLED: 'status-neutral',
  INCONCLUSIVE: 'status-neutral',
}

export function StatusPill({ value }: { value: string | number | null | undefined }) {
  const text = String(value ?? '—')
  return <span className={`status-pill ${statusClass[text] ?? 'status-neutral'}`}>{text}</span>
}
