SCHEMA_VERSION = "4"

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS obs_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obs_projects (
    id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    service_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obs_traces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES obs_projects(id) ON DELETE RESTRICT,
    operation_name TEXT NOT NULL,
    session_id TEXT,
    user_id TEXT,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms REAL,
    otel_trace_id TEXT NOT NULL,
    otel_root_span_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS obs_trace_links (
    id TEXT PRIMARY KEY,
    source_trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    target_trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_trace_id, target_trace_id, relation_type)
);

CREATE TABLE IF NOT EXISTS obs_runs (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    thread_id TEXT,
    trigger_type TEXT NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    agent_name TEXT,
    workflow_name TEXT,
    model_provider TEXT,
    model_name TEXT,
    status TEXT NOT NULL,
    stop_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS obs_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    parent_span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    component_name TEXT,
    operation_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS obs_events (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    stage TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    UNIQUE(trace_id, sequence)
);

CREATE TABLE IF NOT EXISTS obs_model_calls (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    prompt_key TEXT,
    prompt_version TEXT,
    prompt_version_id TEXT,
    evaluation_case_id TEXT,
    experiment_variant_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    cached_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost REAL,
    latency_ms REAL,
    time_to_first_token_ms REAL,
    finish_reason TEXT,
    structured_output_valid INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    input_summary_json TEXT NOT NULL DEFAULT '{}',
    output_summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obs_tool_calls (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    adapter_key TEXT,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    result_summary_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    timeout_seconds REAL,
    latency_ms REAL,
    external_request_id TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(trace_id, tool_call_id)
);

CREATE TABLE IF NOT EXISTS obs_issues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES obs_projects(id) ON DELETE RESTRICT,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    component TEXT,
    status TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    affected_trace_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    reopened_at TEXT,
    latest_context_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS obs_issue_occurrences (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES obs_issues(id) ON DELETE RESTRICT,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    fingerprint TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    component TEXT,
    error_code TEXT,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obs_issue_trace_links (
    issue_id TEXT NOT NULL REFERENCES obs_issues(id) ON DELETE RESTRICT,
    trace_id TEXT NOT NULL REFERENCES obs_traces(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(issue_id, trace_id)
);

CREATE TABLE IF NOT EXISTS obs_metric_points (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES obs_projects(id) ON DELETE RESTRICT,
    trace_id TEXT REFERENCES obs_traces(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES obs_runs(id) ON DELETE RESTRICT,
    span_id TEXT REFERENCES obs_spans(id) ON DELETE RESTRICT,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    dimensions_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_traces_project_time
    ON obs_traces(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_trace_links_source
    ON obs_trace_links(source_trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_obs_trace_links_target
    ON obs_trace_links(target_trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_obs_runs_trace ON obs_runs(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_obs_spans_trace ON obs_spans(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_obs_events_trace ON obs_events(trace_id, sequence);
CREATE INDEX IF NOT EXISTS idx_obs_model_calls_trace ON obs_model_calls(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_obs_tool_calls_trace ON obs_tool_calls(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_obs_issues_project_status
    ON obs_issues(project_id, status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_occurrences_issue
    ON obs_issue_occurrences(issue_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_metrics_name_time
    ON obs_metric_points(metric_name, recorded_at DESC);
"""
