export interface ProductConfig {
  siteId: string
  ownerActorId: string
  agentActorId: string
  executorActorId: string
}

export interface Site {
  id: string
  name: string
  domain: string
  default_country: string
  default_language: string
  timezone: string
  status: string
}

export interface Actor {
  id: string
  actor_type: string
  name: string
  email?: string | null
  status: string
}

export interface WorkbenchSummary {
  pending_approvals: number
  high_value_opportunities: number
  medium_opportunities: number
  active_tasks: number
  active_scan_runs: number
  active_monitoring_plans: number
  recent_work_records: WorkRecord[]
}

export interface WorkRecord {
  id: string
  record_type: string
  title: string
  summary: string
  occurred_at: string
  payload_json?: string
}

export interface Opportunity {
  id: string
  title: string
  normalized_intent: string
  opportunity_type: string
  current_score: number
  priority_level: 'HIGH' | 'MEDIUM' | 'LOW'
  status: string
  current_summary?: string | null
  first_seen_at: string
  last_seen_at: string
  seen_count: number
  reopened_at?: string | null
  observations?: Record<string, unknown>[]
  keywords?: Array<{ id: string; normalized_keyword: string; keyword_role: string }>
  status_history?: Record<string, unknown>[]
  approval_actions?: ApprovalAction[]
  tasks?: ExecutionTask[]
}

export interface ApprovalAction {
  id: string
  action_type: string
  risk_level: string
  status: string
  target_type: string
  target_id: string
  requested_payload_json?: string
  requested_at: string
  next_remind_at?: string | null
}

export interface WorkSchedule {
  id: string
  name: string
  schedule_type: string
  frequency: string
  execution_time?: string | null
  weekday?: number | null
  timezone: string
  enabled: number
  next_run_at?: string | null
  last_run_at?: string | null
}

export interface ScheduleRun {
  id: string
  trigger_type: string
  status: string
  started_at?: string | null
  completed_at?: string | null
  skip_reason?: string | null
  existing_run_id?: string | null
}

export interface ExecutionTask {
  id: string
  title: string
  task_type: string
  status: string
  priority: string
  current_prd_version_id?: string | null
  deadline_at?: string | null
  created_at: string
  opportunities?: Opportunity[]
  assignments?: Array<Record<string, unknown>>
  prd_versions?: PrdVersion[]
  dispatches?: Array<Record<string, unknown>>
  deliveries?: Delivery[]
  publish_records?: Array<Record<string, unknown>>
  status_history?: Array<Record<string, unknown>>
}

export interface PrdVersion {
  id: string
  title: string
  version_no: number
  status: string
  markdown_content: string
  created_at: string
}

export interface Delivery {
  id: string
  title: string
  summary?: string | null
  version_no: number
  status: string
  submitted_at: string
  reviews?: Array<Record<string, unknown>>
}

export interface NotificationSubscription {
  id: string
  recipient_email: string
  recipient_role: string
  enabled: number
  is_default_approver: number
  channel: string
}

export interface NotificationPreference {
  id: string
  event_key: string
  name: string
  description?: string | null
  enabled: number
  mandatory: number
  delivery_mode: string
  supports_threshold: number
  supports_schedule: number
}

export interface SystemStatus {
  database_started: boolean
  checkpoint_started: boolean
  adapters: Array<{
    category: string
    key: string
    display_name: string
    implemented: boolean
    capabilities: string[]
  }>
}

export interface ObservabilityTrace {
  id: string
  operation_name: string
  status: string
  session_id?: string | null
  user_id?: string | null
  error_code?: string | null
  error_message?: string | null
  started_at: string
  completed_at?: string | null
  duration_ms?: number | null
  runs?: ObservabilityRun[]
  events?: ObservabilityEvent[]
  model_calls?: ModelCallRecord[]
  tool_calls?: ToolCallRecord[]
  issues?: AgentIssue[]
}

export interface ObservabilityRun {
  id: string
  trigger_type: string
  agent_name?: string | null
  workflow_name?: string | null
  model_provider?: string | null
  model_name?: string | null
  status: string
  duration_ms?: number | null
  spans?: ObservabilitySpan[]
}

export interface ObservabilitySpan {
  id: string
  parent_span_id?: string | null
  kind: string
  component_name?: string | null
  operation_name: string
  status: string
  duration_ms?: number | null
  error_code?: string | null
  error_message?: string | null
}

export interface ObservabilityEvent {
  id: string
  sequence: number
  event_type: string
  severity: string
  stage?: string | null
  payload_json: string
  occurred_at: string
}

export interface ModelCallRecord {
  id: string
  provider: string
  model: string
  operation: string
  prompt_key?: string | null
  prompt_version?: string | null
  prompt_version_id?: string | null
  total_tokens?: number | null
  estimated_cost?: number | null
  latency_ms?: number | null
  structured_output_valid?: number | null
  status: string
  error_code?: string | null
  started_at: string
}

export interface ToolCallRecord {
  id: string
  tool_call_id: string
  tool_name: string
  adapter_key?: string | null
  operation: string
  status: string
  latency_ms?: number | null
  retry_count: number
  error_code?: string | null
  error_message?: string | null
  started_at: string
}

export interface AgentIssue {
  id: string
  fingerprint: string
  title: string
  issue_type: string
  severity: string
  component?: string | null
  status: string
  occurrence_count: number
  affected_trace_count: number
  first_seen_at: string
  last_seen_at: string
}

export interface MetricSummary {
  metric_name: string
  unit?: string | null
  point_count: number
  average_value: number
  minimum_value: number
  maximum_value: number
  last_recorded_at: string
}

export interface PromptDefinition {
  id: string
  prompt_key: string
  name: string
  purpose: string
  status: string
  created_at: string
}

export interface PromptVersion {
  id: string
  prompt_id: string
  version_no: number
  version_label: string
  stage: string
  system_template: string
  user_template: string
  output_schema_json: string
  config_json: string
  content_hash: string
  parent_version_id?: string | null
  change_summary?: string | null
  created_by?: string | null
  created_at: string
}

export interface PromptDeployment {
  id: string
  prompt_id: string
  environment: string
  current_version_id?: string | null
  canary_version_id?: string | null
  canary_percentage: number
  updated_at: string
}

export interface PromptDataset {
  id: string
  prompt_id?: string | null
  name: string
  description?: string | null
  status: string
  created_at: string
}

export interface EvaluationRun {
  id: string
  prompt_version_id: string
  dataset_id: string
  model_provider: string
  model_name: string
  status: string
  case_count: number
  passed_count: number
  pass_rate: number
  average_score: number
  total_cost: number
  average_latency_ms: number
  started_at: string
}

export interface PromptExperiment {
  id: string
  prompt_id: string
  baseline_version_id: string
  challenger_version_id: string
  dataset_id: string
  status: string
  recommendation?: string | null
  created_at: string
}
