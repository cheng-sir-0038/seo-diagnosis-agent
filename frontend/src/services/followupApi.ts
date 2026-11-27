import { agentApiUrl } from './agentApi'

export const followupActions = {
  prepare: '整理复诊前准备清单',
  confirm: '确认保存资料与待办',
  finish: '结束本次整理',
} as const

export type FollowupAction = typeof followupActions[keyof typeof followupActions]

export interface RecordFields {
  record_date: string | null
  value: string | null
  unit: string | null
  specimen: string | null
  method: string | null
}

export interface MedicalRecord extends RecordFields {
  kind: 'report' | 'visit_plan' | 'self_report' | 'purchase' | 'medication_note' | 'visit_summary'
  item: string
  note: string | null
  reference_range: string | null
  flag: string | null
  due_date: string | null
  plan_status: 'unknown' | 'self_reported_done' | 'documented_done' | null
}

export interface Evidence {
  id: string
  source: string
  record: MedicalRecord
  original: MedicalRecord | null
}

export interface ReviewIssue {
  item: string
  text: string
  source: string | null
}

export interface ChecklistItem {
  id: string
  title: string
  detail?: string
  gaps?: string[]
  due_date?: string
  plan_status?: NonNullable<MedicalRecord['plan_status']>
  sources: { record_id: string, source: string }[]
}

export interface FollowupCase {
  subject_id: string | null
  demo: boolean
  revision: number
  files: { name: string, digest: string, uploaded_at: string, rows: number, error: string | null }[]
  records: Evidence[]
  report_review: { comparable_pairs: number, issues: ReviewIssue[] }
  draft: {
    revision?: number
    checklist?: {
      materials_to_bring: ChecklistItem[]
      existing_arrangements: ChecklistItem[]
      questions_for_doctor: ChecklistItem[]
    }
    comparisons: { item: string, earlier: string, later: string, delta: string | null, unit: string | null, reasons: string[] }[]
    issues: ReviewIssue[]
    plans: string[]
    pending_plans: string[]
  } | null
  saved: {
    saved_at: string
    tasks: { id: string, title: string, due_date: string | null, source: string }[]
  } | null
}

export async function followupRequest(
  runId: string, access: string, path = '', options: RequestInit = {},
): Promise<FollowupCase> {
  const headers = new Headers(options.headers)
  headers.set('X-Followup-Key', access)
  const response = await fetch(agentApiUrl(`/api/v1/chat/runs/${encodeURIComponent(runId)}/followup${path}`), {
    ...options, headers, cache: 'no-store',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '此操作未能完成。')
  }
  return response.json()
}
