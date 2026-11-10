import type {
  Actor,
  ApprovalAction,
  ExecutionTask,
  NotificationPreference,
  NotificationSubscription,
  Opportunity,
  ProductConfig,
  ScheduleRun,
  Site,
  SystemStatus,
  WorkSchedule,
  WorkbenchSummary,
  AgentIssue,
  EvaluationRun,
  MetricSummary,
  ObservabilityTrace,
  PromptDataset,
  PromptDefinition,
  PromptDeployment,
  PromptExperiment,
  PromptVersion,
} from '../types/product'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    let detail = `请求失败：HTTP ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown; message?: string }
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail && typeof body.detail === 'object') detail = JSON.stringify(body.detail)
      else if (body.message) detail = body.message
    } catch {
      // Keep generic message for non-JSON errors.
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const productApi = {
  optimizePrompt: (
    promptId: string,
    payload: {
      dataset_id: string
      failures: Record<string, unknown>[]
      success_criteria: Record<string, unknown>
      environment?: string
      canary_percentage?: number
    },
  ) =>
    request<{
      optimization_run_id: string
      challenger_version_id: string
      experiment_id: string
      recommendation: string
    }>(`/api/v1/seo-agent/automation/prompts/${promptId}/optimize`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listObservabilityTraces: (status?: string) =>
    request<ObservabilityTrace[]>(
      `/api/v1/observability/traces${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),
  getObservabilityTrace: (traceId: string) =>
    request<ObservabilityTrace>(`/api/v1/observability/traces/${traceId}`),
  listAgentIssues: (status?: string) =>
    request<AgentIssue[]>(
      `/api/v1/observability/issues${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),
  setAgentIssueStatus: (issueId: string, status: string) =>
    request<AgentIssue>(`/api/v1/observability/issues/${issueId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  listMetricSummary: (metricName?: string) =>
    request<MetricSummary[]>(
      `/api/v1/observability/metrics${metricName ? `?metric_name=${encodeURIComponent(metricName)}` : ''}`,
    ),
  listPrompts: () => request<PromptDefinition[]>('/api/v1/promptops/prompts'),
  createPrompt: (payload: { prompt_key: string; name: string; purpose: string }) =>
    request<PromptDefinition>('/api/v1/promptops/prompts', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listPromptVersions: (promptId: string) =>
    request<PromptVersion[]>(`/api/v1/promptops/prompts/${promptId}/versions`),
  createPromptVersion: (
    promptId: string,
    payload: {
      system_template: string
      user_template: string
      output_schema: Record<string, unknown>
      config?: Record<string, unknown>
      stage?: string
      parent_version_id?: string
      change_summary?: string
      created_by?: string
    },
  ) =>
    request<PromptVersion>(`/api/v1/promptops/prompts/${promptId}/versions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listPromptDeployments: (promptId: string) =>
    request<PromptDeployment[]>(`/api/v1/promptops/prompts/${promptId}/deployments`),
  startPromptCanary: (promptId: string, versionId: string, percentage = 10) =>
    request<PromptDeployment>(`/api/v1/promptops/prompts/${promptId}/canary`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId, environment: 'production', percentage }),
    }),
  promotePromptVersion: (promptId: string, versionId: string, reason?: string) =>
    request<PromptDeployment>(`/api/v1/promptops/prompts/${promptId}/promote`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId, environment: 'production', reason }),
    }),
  rollbackPromptVersion: (promptId: string, versionId: string, reason: string) =>
    request<PromptDeployment>(`/api/v1/promptops/prompts/${promptId}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId, environment: 'production', reason }),
    }),
  listPromptDatasets: (promptId?: string) =>
    request<PromptDataset[]>(
      `/api/v1/promptops/datasets${promptId ? `?prompt_id=${encodeURIComponent(promptId)}` : ''}`,
    ),
  createPromptDataset: (payload: { name: string; prompt_id?: string; description?: string }) =>
    request<PromptDataset>('/api/v1/promptops/datasets', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  addPromptDatasetCase: (
    datasetId: string,
    payload: {
      name: string
      input_data: unknown
      expected_output?: unknown
      grading_rules?: Record<string, unknown>
      tags?: string[]
      source_trace_id?: string
      source_issue_id?: string
    },
  ) =>
    request(`/api/v1/promptops/datasets/${datasetId}/cases`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listEvaluationRuns: (promptVersionId?: string) =>
    request<EvaluationRun[]>(
      `/api/v1/promptops/evaluations${promptVersionId ? `?prompt_version_id=${encodeURIComponent(promptVersionId)}` : ''}`,
    ),
  listPromptExperiments: (promptId?: string) =>
    request<PromptExperiment[]>(
      `/api/v1/promptops/experiments${promptId ? `?prompt_id=${encodeURIComponent(promptId)}` : ''}`,
    ),
  listSites: () => request<Site[]>('/api/v1/seo-agent/system/sites'),
  getSite: (siteId: string) => request<Site>(`/api/v1/seo-agent/system/sites/${siteId}`),
  createSite: (payload: {
    name: string
    domain: string
    default_country: string
    default_language: string
    timezone: string
  }) => request<Site>('/api/v1/seo-agent/system/sites', { method: 'POST', body: JSON.stringify(payload) }),
  listActors: (actorType?: string) =>
    request<Actor[]>(`/api/v1/seo-agent/system/actors${actorType ? `?actor_type=${encodeURIComponent(actorType)}` : ''}`),
  createActor: (payload: { actor_type: string; name: string; email?: string }) =>
    request<Actor>('/api/v1/seo-agent/system/actors', { method: 'POST', body: JSON.stringify(payload) }),
  getSystemStatus: () => request<SystemStatus>('/api/v1/seo-agent/system/status'),
  getWorkbench: (siteId: string) =>
    request<WorkbenchSummary>(`/api/v1/seo-agent/workbench/summary?site_id=${encodeURIComponent(siteId)}`),
  listOpportunities: (siteId: string, priority?: string) =>
    request<Opportunity[]>(
      `/api/v1/seo-agent/opportunities?site_id=${encodeURIComponent(siteId)}${priority ? `&priority=${priority}` : ''}`,
    ),
  getOpportunity: (id: string) => request<Opportunity>(`/api/v1/seo-agent/opportunities/${id}/detail`),
  rejectOpportunity: (id: string, actorId: string, reason: string) =>
    request<Opportunity>(`/api/v1/seo-agent/opportunities/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ actor_id: actorId, reason }),
    }),
  listApprovals: (siteId: string) =>
    request<ApprovalAction[]>(`/api/v1/seo-agent/approvals?site_id=${encodeURIComponent(siteId)}`),
  decideApproval: (
    id: string,
    payload: { actor_id: string; decision: string; edited_payload?: Record<string, unknown>; reason?: string },
  ) => request(`/api/v1/seo-agent/approvals/${id}/decisions`, { method: 'POST', body: JSON.stringify(payload) }),
  listSchedules: (siteId: string) =>
    request<WorkSchedule[]>(`/api/v1/seo-agent/schedules?site_id=${encodeURIComponent(siteId)}`),
  createSchedule: (payload: {
    site_id: string
    schedule_type: string
    name: string
    frequency: string
    timezone: string
    execution_time?: string
    weekday?: number
    config?: Record<string, unknown>
  }) => request<WorkSchedule>('/api/v1/seo-agent/schedules', { method: 'POST', body: JSON.stringify(payload) }),
  setScheduleEnabled: (id: string, enabled: boolean) =>
    request<WorkSchedule>(`/api/v1/seo-agent/schedules/${id}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' }),
  listScheduleRuns: (id: string) => request<ScheduleRun[]>(`/api/v1/seo-agent/schedules/${id}/runs`),
  startScan: (scheduleId: string, triggerType: 'MANUAL' | 'SCHEDULED' = 'MANUAL') =>
    request<ScheduleRun>('/api/v1/seo-agent/scan-runs', {
      method: 'POST',
      body: JSON.stringify({
        schedule_id: scheduleId,
        trigger_type: triggerType,
        idempotency_key: `scan:${scheduleId}:${crypto.randomUUID()}`,
      }),
    }),
  listTasks: (siteId: string) =>
    request<ExecutionTask[]>(`/api/v1/seo-agent/tasks?site_id=${encodeURIComponent(siteId)}`),
  getTask: (id: string) => request<ExecutionTask>(`/api/v1/seo-agent/tasks/${id}/detail`),
  createTask: (payload: {
    site_id: string
    title: string
    task_type: string
    priority: string
    opportunity_ids: string[]
    owner_actor_id: string
    deadline_at?: string
  }) => request<ExecutionTask>('/api/v1/seo-agent/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  renderPrdArtifacts: (prdId: string) =>
    request<Array<Record<string, unknown>>>(`/api/v1/seo-agent/tasks/prd-versions/${prdId}/artifacts`, { method: 'POST' }),
  generateTaskPrd: (taskId: string, actorId: string) =>
    request<Record<string, unknown>>(`/api/v1/seo-agent/automation/tasks/${taskId}/generate-prd`, {
      method: 'POST',
      body: JSON.stringify({ actor_id: actorId }),
    }),
  reviewDeliveryWithAgent: (deliveryId: string, actorId: string) =>
    request<Record<string, unknown>>(`/api/v1/seo-agent/automation/deliveries/${deliveryId}/review`, {
      method: 'POST',
      body: JSON.stringify({ actor_id: actorId }),
    }),
  getAutomationStatus: () =>
    request<{ smtp: { status: string; message?: string | null }; scheduler_running: boolean; jobs: string[] }>(
      '/api/v1/seo-agent/automation/status',
    ),
  listSubscriptions: (siteId: string) =>
    request<NotificationSubscription[]>(
      `/api/v1/seo-agent/notifications/subscriptions?site_id=${encodeURIComponent(siteId)}`,
    ),
  createSubscription: (payload: {
    site_id: string
    subscriber_actor_id?: string
    recipient_email: string
    recipient_role: string
    is_default_approver: boolean
  }) =>
    request<NotificationSubscription>('/api/v1/seo-agent/notifications/subscriptions', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listPreferences: (subscriptionId: string) =>
    request<NotificationPreference[]>(
      `/api/v1/seo-agent/notifications/subscriptions/${subscriptionId}/preferences`,
    ),
  setPreference: (subscriptionId: string, eventKey: string, enabled: boolean) =>
    request<NotificationPreference>(
      `/api/v1/seo-agent/notifications/subscriptions/${subscriptionId}/preferences/${eventKey}`,
      { method: 'PUT', body: JSON.stringify({ enabled }) },
    ),
}

export const PRODUCT_CONFIG_KEY = 'seo-agent-product-config-v1'

export function loadProductConfig(): ProductConfig | null {
  try {
    const raw = localStorage.getItem(PRODUCT_CONFIG_KEY)
    return raw ? (JSON.parse(raw) as ProductConfig) : null
  } catch {
    return null
  }
}

export function saveProductConfig(config: ProductConfig): void {
  localStorage.setItem(PRODUCT_CONFIG_KEY, JSON.stringify(config))
}

export function clearProductConfig(): void {
  localStorage.removeItem(PRODUCT_CONFIG_KEY)
}
