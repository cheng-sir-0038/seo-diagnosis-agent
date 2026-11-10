from __future__ import annotations

# ruff: noqa: E501

SCHEMA_VERSION = 3

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    default_country TEXT NOT NULL,
    default_language TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS actors (
    id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    external_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS adapter_definitions (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    adapter_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    implementation_status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    config_schema_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(category, adapter_key)
);

CREATE TABLE IF NOT EXISTS site_adapter_bindings (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    adapter_definition_id TEXT NOT NULL REFERENCES adapter_definitions(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
    config_json TEXT NOT NULL DEFAULT '{}',
    secret_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(site_id, adapter_definition_id)
);

CREATE TABLE IF NOT EXISTS work_schedules (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    schedule_type TEXT NOT NULL,
    name TEXT NOT NULL,
    frequency TEXT NOT NULL,
    weekday INTEGER,
    execution_time TEXT,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    next_run_at TEXT,
    last_run_at TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES work_schedules(id) ON DELETE RESTRICT,
    trigger_type TEXT NOT NULL,
    scheduled_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,
    skip_reason TEXT,
    existing_run_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_threads (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    thread_id TEXT NOT NULL UNIQUE,
    workflow_type TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    business_object_type TEXT NOT NULL,
    business_object_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    current_checkpoint_id TEXT,
    created_at TEXT NOT NULL,
    last_resumed_at TEXT,
    completed_at TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    workflow_thread_id TEXT NOT NULL REFERENCES workflow_threads(id) ON DELETE RESTRICT,
    schedule_run_id TEXT REFERENCES schedule_runs(id) ON DELETE RESTRICT,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    model_provider TEXT,
    model_name TEXT,
    prompt_version TEXT,
    token_usage_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    stop_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runtime_tasks (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    conversation_id TEXT,
    workflow_thread_id TEXT NOT NULL REFERENCES workflow_threads(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    user_intent TEXT NOT NULL,
    status TEXT NOT NULL,
    status_group TEXT NOT NULL,
    current_stage TEXT,
    current_run_id TEXT,
    survival_summary_json TEXT NOT NULL DEFAULT '{}',
    prompt_version TEXT,
    prompt_hash TEXT,
    available_tools_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_run_events (
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(agent_run_id, sequence)
);

CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    checkpoint_no INTEGER NOT NULL,
    stage TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    tool_count INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT NOT NULL,
    pending_tool_calls_json TEXT NOT NULL DEFAULT '[]',
    token_usage_json TEXT NOT NULL DEFAULT '{}',
    survival_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(agent_run_id, checkpoint_no)
);

CREATE TABLE IF NOT EXISTS agent_survival_summaries (
    id TEXT PRIMARY KEY,
    agent_runtime_task_id TEXT NOT NULL REFERENCES agent_runtime_tasks(id) ON DELETE RESTRICT,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    checkpoint_id TEXT NOT NULL REFERENCES agent_run_checkpoints(id) ON DELETE RESTRICT,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    id TEXT PRIMARY KEY,
    normalized_keyword TEXT NOT NULL,
    language TEXT NOT NULL,
    country TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(normalized_keyword, language, country)
);

CREATE TABLE IF NOT EXISTS competitor_pages (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    competitor_domain TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(site_id, url)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    fingerprint TEXT NOT NULL,
    normalized_intent TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    target_country TEXT NOT NULL,
    target_language TEXT NOT NULL,
    title TEXT NOT NULL,
    current_score INTEGER,
    priority_level TEXT,
    status TEXT NOT NULL,
    current_summary TEXT,
    policy_version TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    rejected_at TEXT,
    rejection_reason TEXT,
    reopened_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(site_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS opportunity_observations (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    score INTEGER NOT NULL,
    priority_level TEXT NOT NULL,
    search_volume REAL,
    keyword_difficulty REAL,
    competitor_count INTEGER,
    gap_summary TEXT,
    score_factors_json TEXT NOT NULL DEFAULT '{}',
    policy_version TEXT NOT NULL,
    threshold_snapshot_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_status_history (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT NOT NULL,
    threshold_snapshot_json TEXT NOT NULL DEFAULT '{}',
    triggered_conditions_json TEXT NOT NULL DEFAULT '[]',
    changed_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    changed_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnosis_hypotheses (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    confidence TEXT,
    conclusion TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL REFERENCES diagnosis_hypotheses(id) ON DELETE RESTRICT,
    evidence_snapshot_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    relevance_score REAL,
    explanation TEXT,
    linked_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(hypothesis_id, evidence_snapshot_id, relation_type)
);

CREATE TABLE IF NOT EXISTS opportunity_observation_evidence (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES opportunity_observations(id) ON DELETE RESTRICT,
    evidence_snapshot_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    contribution_weight REAL,
    created_at TEXT NOT NULL,
    UNIQUE(observation_id, evidence_snapshot_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS opportunity_keywords (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    keyword_id TEXT NOT NULL REFERENCES keywords(id) ON DELETE RESTRICT,
    keyword_role TEXT NOT NULL,
    relevance_score REAL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(opportunity_id, keyword_id, keyword_role)
);

CREATE TABLE IF NOT EXISTS opportunity_competitor_pages (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    competitor_page_id TEXT NOT NULL REFERENCES competitor_pages(id) ON DELETE RESTRICT,
    gap_type TEXT NOT NULL,
    relevance_score REAL,
    evidence_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(opportunity_id, competitor_page_id, gap_type)
);

CREATE TABLE IF NOT EXISTS approval_actions (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    action_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    requested_payload_json TEXT NOT NULL DEFAULT '{}',
    requested_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    requested_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_at TEXT NOT NULL,
    next_remind_at TEXT,
    last_reminded_at TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS approval_action_evidence (
    id TEXT PRIMARY KEY,
    approval_action_id TEXT NOT NULL REFERENCES approval_actions(id) ON DELETE RESTRICT,
    evidence_snapshot_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(approval_action_id, evidence_snapshot_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS approval_action_approvers (
    id TEXT PRIMARY KEY,
    approval_action_id TEXT NOT NULL REFERENCES approval_actions(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    approval_order INTEGER NOT NULL DEFAULT 1,
    is_required INTEGER NOT NULL DEFAULT 1 CHECK(is_required IN (0, 1)),
    status TEXT NOT NULL,
    notified_at TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(approval_action_id, actor_id)
);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id TEXT PRIMARY KEY,
    approval_action_approver_id TEXT NOT NULL REFERENCES approval_action_approvers(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL,
    original_payload_json TEXT NOT NULL DEFAULT '{}',
    edited_payload_json TEXT,
    reason TEXT,
    decided_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    decided_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_tasks (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    current_prd_version_id TEXT,
    deadline_at TEXT,
    created_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS task_opportunities (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
    contribution_weight REAL,
    linked_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, opportunity_id)
);

CREATE TABLE IF NOT EXISTS task_assignments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    assignment_role TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    accepted_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, actor_id, assignment_role)
);

CREATE TABLE IF NOT EXISTS task_status_history (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    changed_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    changed_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_prd_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    structured_content_json TEXT NOT NULL,
    markdown_content TEXT NOT NULL,
    prompt_version_id TEXT,
    source_trace_id TEXT,
    generated_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    source_version_id TEXT REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    change_summary TEXT,
    created_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version_no)
);

CREATE TABLE IF NOT EXISTS task_prd_evidence (
    id TEXT PRIMARY KEY,
    prd_version_id TEXT NOT NULL REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    evidence_snapshot_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    section_key TEXT NOT NULL,
    citation_label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(prd_version_id, evidence_snapshot_id, evidence_role, section_key)
);

CREATE TABLE IF NOT EXISTS task_prd_artifacts (
    id TEXT PRIMARY KEY,
    prd_version_id TEXT NOT NULL REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    renderer_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    checksum TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(prd_version_id, renderer_key, checksum)
);

CREATE TABLE IF NOT EXISTS task_dispatches (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    prd_version_id TEXT NOT NULL REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    dispatcher_adapter TEXT NOT NULL,
    recipient_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    external_reference TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    dispatched_at TEXT,
    accepted_at TEXT,
    failed_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    prd_version_id TEXT NOT NULL REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL,
    parent_delivery_id TEXT REFERENCES deliveries(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    structured_content_json TEXT NOT NULL DEFAULT '{}',
    submitted_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    submitted_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version_no)
);

CREATE TABLE IF NOT EXISTS delivery_artifacts (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id) ON DELETE RESTRICT,
    artifact_type TEXT NOT NULL,
    file_name TEXT,
    file_path TEXT,
    content_url TEXT,
    content_type TEXT,
    checksum TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_reviews (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id) ON DELETE RESTRICT,
    prd_version_id TEXT NOT NULL REFERENCES task_prd_versions(id) ON DELETE RESTRICT,
    reviewer_type TEXT NOT NULL,
    reviewer_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    score INTEGER,
    passed_items_json TEXT NOT NULL DEFAULT '[]',
    failed_items_json TEXT NOT NULL DEFAULT '[]',
    missing_items_json TEXT NOT NULL DEFAULT '[]',
    revision_instructions_json TEXT NOT NULL DEFAULT '[]',
    prompt_version_id TEXT,
    source_trace_id TEXT,
    reviewed_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_review_evidence (
    id TEXT PRIMARY KEY,
    delivery_review_id TEXT NOT NULL REFERENCES delivery_reviews(id) ON DELETE RESTRICT,
    evidence_snapshot_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    review_item_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(delivery_review_id, evidence_snapshot_id, evidence_role, review_item_key)
);

CREATE TABLE IF NOT EXISTS publish_records (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES execution_tasks(id) ON DELETE RESTRICT,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id) ON DELETE RESTRICT,
    publisher_adapter TEXT NOT NULL,
    status TEXT NOT NULL,
    target_environment TEXT NOT NULL,
    candidate_url TEXT,
    published_url TEXT,
    published_at TEXT,
    submitted_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    confirmed_by_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    confirmed_at TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    approval_action_id TEXT REFERENCES approval_actions(id) ON DELETE RESTRICT,
    source_trace_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_plans (
    id TEXT PRIMARY KEY,
    publish_record_id TEXT NOT NULL REFERENCES publish_records(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    origin_trace_id TEXT,
    indexing_started_at TEXT NOT NULL,
    indexed_at TEXT,
    ranking_started_at TEXT,
    ranking_ends_at TEXT,
    next_check_at TEXT,
    final_outcome TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_goals (
    id TEXT PRIMARY KEY,
    monitoring_plan_id TEXT NOT NULL REFERENCES monitoring_plans(id) ON DELETE RESTRICT,
    goal_type TEXT NOT NULL,
    target_keyword TEXT,
    target_value REAL,
    comparison_operator TEXT,
    minimum_improvement REAL,
    baseline_value REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_checks (
    id TEXT PRIMARY KEY,
    monitoring_plan_id TEXT NOT NULL REFERENCES monitoring_plans(id) ON DELETE RESTRICT,
    check_type TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,
    conclusion TEXT,
    trace_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    error_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_observations (
    id TEXT PRIMARY KEY,
    monitoring_check_id TEXT NOT NULL REFERENCES monitoring_checks(id) ON DELETE RESTRICT,
    metric_type TEXT NOT NULL,
    keyword_id TEXT REFERENCES keywords(id) ON DELETE RESTRICT,
    value_numeric REAL,
    value_text TEXT,
    previous_value_numeric REAL,
    change_value REAL,
    evidence_snapshot_id TEXT,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_outcomes (
    id TEXT PRIMARY KEY,
    monitoring_plan_id TEXT NOT NULL REFERENCES monitoring_plans(id) ON DELETE RESTRICT,
    outcome TEXT NOT NULL,
    confidence TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_summary_json TEXT NOT NULL DEFAULT '{}',
    decided_by_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_outcome_goals (
    id TEXT PRIMARY KEY,
    monitoring_outcome_id TEXT NOT NULL REFERENCES monitoring_outcomes(id) ON DELETE RESTRICT,
    monitoring_goal_id TEXT NOT NULL REFERENCES monitoring_goals(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    actual_value REAL,
    target_value_snapshot REAL,
    improvement_percent REAL,
    explanation TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(monitoring_outcome_id, monitoring_goal_id)
);

CREATE TABLE IF NOT EXISTS notification_subscriptions (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    subscriber_actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    recipient_email TEXT NOT NULL,
    recipient_role TEXT NOT NULL,
    channel TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    is_default_approver INTEGER NOT NULL DEFAULT 0 CHECK(is_default_approver IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_event_types (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    default_delivery_mode TEXT NOT NULL,
    supports_threshold INTEGER NOT NULL DEFAULT 0 CHECK(supports_threshold IN (0, 1)),
    supports_schedule INTEGER NOT NULL DEFAULT 0 CHECK(supports_schedule IN (0, 1)),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_preferences (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES notification_subscriptions(id) ON DELETE RESTRICT,
    event_type_id TEXT NOT NULL REFERENCES notification_event_types(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    mandatory INTEGER NOT NULL DEFAULT 0 CHECK(mandatory IN (0, 1)),
    delivery_mode TEXT NOT NULL,
    schedule_json TEXT NOT NULL DEFAULT '{}',
    threshold_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(subscription_id, event_type_id)
);

CREATE TABLE IF NOT EXISTS notification_events (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    event_type_id TEXT NOT NULL REFERENCES notification_event_types(id) ON DELETE RESTRICT,
    business_object_type TEXT NOT NULL,
    business_object_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content_json TEXT NOT NULL,
    priority TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    notification_event_id TEXT NOT NULL REFERENCES notification_events(id) ON DELETE RESTRICT,
    subscription_id TEXT NOT NULL REFERENCES notification_subscriptions(id) ON DELETE RESTRICT,
    channel_adapter TEXT NOT NULL,
    provider_adapter TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_message_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    scheduled_at TEXT,
    sent_at TEXT,
    failed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id TEXT PRIMARY KEY,
    notification_delivery_id TEXT NOT NULL REFERENCES notification_deliveries(id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    response_code TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(notification_delivery_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_call_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_relations (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, object_type, object_id, relation_type)
);

CREATE TABLE IF NOT EXISTS work_records (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    workflow_thread_id TEXT REFERENCES workflow_threads(id) ON DELETE RESTRICT,
    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE RESTRICT,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    actor_id TEXT REFERENCES actors(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_record_relations (
    id TEXT PRIMARY KEY,
    work_record_id TEXT NOT NULL REFERENCES work_records(id) ON DELETE RESTRICT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    relation_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_record_id, object_type, object_id, relation_role)
);

CREATE INDEX IF NOT EXISTS idx_site_bindings_site ON site_adapter_bindings(site_id);
CREATE INDEX IF NOT EXISTS idx_schedules_site ON work_schedules(site_id, schedule_type, enabled);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_runs_thread ON agent_runs(workflow_thread_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runtime_tasks_site_status
ON agent_runtime_tasks(site_id, status_group, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
ON agent_run_events(agent_run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_agent_run_checkpoints_run
ON agent_run_checkpoints(agent_run_id, checkpoint_no);
CREATE INDEX IF NOT EXISTS idx_opportunity_status ON opportunities(site_id, status, priority_level);
CREATE INDEX IF NOT EXISTS idx_observations_opportunity ON opportunity_observations(opportunity_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_approval_waiting ON approval_actions(site_id, status, next_remind_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON execution_tasks(site_id, status, deadline_at);
CREATE INDEX IF NOT EXISTS idx_monitoring_checks_due ON monitoring_checks(status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_notification_due ON notification_deliveries(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_work_records_site ON work_records(site_id, occurred_at);

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_opportunity_schedule
ON work_schedules(site_id)
WHERE schedule_type = 'OPPORTUNITY_SCAN' AND enabled = 1 AND archived_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_task_owner
ON task_assignments(task_id)
WHERE assignment_role = 'OWNER' AND status IN ('ASSIGNED', 'ACCEPTED');


CREATE UNIQUE INDEX IF NOT EXISTS ux_running_schedule_run
ON schedule_runs(schedule_id)
WHERE status = 'RUNNING';
"""
