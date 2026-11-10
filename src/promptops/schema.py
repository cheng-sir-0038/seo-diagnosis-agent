PROMPTOPS_SCHEMA_VERSION = "1"

PROMPTOPS_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS prompt_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_definitions (
    id TEXT PRIMARY KEY,
    prompt_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL,
    version_label TEXT NOT NULL,
    stage TEXT NOT NULL,
    system_template TEXT NOT NULL,
    user_template TEXT NOT NULL,
    output_schema_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    parent_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    change_summary TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(prompt_id, version_no),
    UNIQUE(prompt_id, content_hash)
);

CREATE TABLE IF NOT EXISTS prompt_deployments (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    current_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    canary_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    canary_percentage INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(prompt_id, environment)
);

CREATE TABLE IF NOT EXISTS prompt_datasets (
    id TEXT PRIMARY KEY,
    prompt_id TEXT REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_dataset_cases (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES prompt_datasets(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    expected_output_json TEXT NOT NULL DEFAULT '{}',
    grading_rules_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_trace_id TEXT,
    source_issue_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_evaluation_runs (
    id TEXT PRIMARY KEY,
    prompt_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    dataset_id TEXT NOT NULL REFERENCES prompt_datasets(id) ON DELETE RESTRICT,
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL,
    case_count INTEGER NOT NULL DEFAULT 0,
    passed_count INTEGER NOT NULL DEFAULT 0,
    pass_rate REAL NOT NULL DEFAULT 0,
    average_score REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    average_latency_ms REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS prompt_evaluation_results (
    id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL REFERENCES prompt_evaluation_runs(id) ON DELETE RESTRICT,
    case_id TEXT NOT NULL REFERENCES prompt_dataset_cases(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    output_json TEXT NOT NULL DEFAULT '{}',
    scores_json TEXT NOT NULL DEFAULT '{}',
    total_score REAL NOT NULL DEFAULT 0,
    passed INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    latency_ms REAL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(evaluation_run_id, case_id)
);

CREATE TABLE IF NOT EXISTS prompt_experiments (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    baseline_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    challenger_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    dataset_id TEXT NOT NULL REFERENCES prompt_datasets(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    success_criteria_json TEXT NOT NULL DEFAULT '{}',
    recommendation TEXT,
    comparison_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS prompt_experiment_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES prompt_experiments(id) ON DELETE RESTRICT,
    variant_key TEXT NOT NULL,
    prompt_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    evaluation_run_id TEXT REFERENCES prompt_evaluation_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id, variant_key)
);

CREATE TABLE IF NOT EXISTS prompt_promotion_history (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    action TEXT NOT NULL,
    from_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    to_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_optimization_runs (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompt_definitions(id) ON DELETE RESTRICT,
    current_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    challenger_version_id TEXT REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    dataset_id TEXT NOT NULL REFERENCES prompt_datasets(id) ON DELETE RESTRICT,
    experiment_id TEXT REFERENCES prompt_experiments(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    failures_json TEXT NOT NULL DEFAULT '[]',
    recommendation TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt
    ON prompt_versions(prompt_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_cases_dataset
    ON prompt_dataset_cases(dataset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_prompt_eval_version
    ON prompt_evaluation_runs(prompt_version_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_experiments_prompt
    ON prompt_experiments(prompt_id, created_at DESC);
"""
