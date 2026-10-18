"""Schema versioning and migration engine for OpenDeal storage."""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple

logger = logging.getLogger(__name__)

MIGRATION_V1 = """
-- Companies Table
CREATE TABLE IF NOT EXISTS companies (
    entity_id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    ticker TEXT,
    primary_exchange TEXT,
    incorporation_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    primary_country TEXT,
    website TEXT,
    sic_code TEXT,
    sic_description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);
CREATE INDEX IF NOT EXISTS idx_companies_jurisdiction ON companies(jurisdiction);

-- Company Identifiers (CIK, LEI, Companies House, CIN, etc.)
CREATE TABLE IF NOT EXISTS company_identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    scheme TEXT NOT NULL,
    identifier TEXT NOT NULL,
    jurisdiction TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    UNIQUE(scheme, identifier)
);

CREATE INDEX IF NOT EXISTS idx_identifiers_lookup ON company_identifiers(scheme, identifier);
CREATE INDEX IF NOT EXISTS idx_identifiers_entity ON company_identifiers(entity_id);

-- Sources Table
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    publisher TEXT NOT NULL,
    document_id TEXT,
    publication_date TEXT,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sources_doc_id ON sources(document_id);

-- Immutable Evidence Table
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    raw_content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    document_name TEXT,
    page_number INTEGER,
    line_number INTEGER,
    section_name TEXT,
    tag_name TEXT,
    confidence_score REAL NOT NULL DEFAULT 1.0,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);

-- Financial & Operational Facts Table
CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    canonical_variable TEXT NOT NULL,
    fact_state TEXT NOT NULL,
    reported_value TEXT,
    normalized_value REAL,
    currency TEXT NOT NULL,
    unit_scale TEXT NOT NULL,
    scale INTEGER NOT NULL DEFAULT 0,
    sign TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    is_instant INTEGER NOT NULL DEFAULT 0,
    accounting_standard TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    missing_reason TEXT NOT NULL,
    conflict_note TEXT,
    parent_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    competing_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    transformation_history_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_query ON facts(entity_id, canonical_variable, fiscal_year, fiscal_period);
CREATE INDEX IF NOT EXISTS idx_facts_state ON facts(fact_state);

-- Fact-to-Evidence Link Table (supports multiple corroborating or competing pieces of evidence)
CREATE TABLE IF NOT EXISTS fact_evidence (
    fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    PRIMARY KEY (fact_id, evidence_id)
);

-- Validation Results
CREATE TABLE IF NOT EXISTS validation_results (
    result_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    severity TEXT NOT NULL,
    observed_value TEXT,
    expected_range TEXT,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_target ON validation_results(target_id);
"""

MIGRATION_V2 = """
-- Entity Relationships Table (Parent, Subsidiary, Affiliate, etc.)
CREATE TABLE IF NOT EXISTS entity_relationships (
    relationship_id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    target_entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    ownership_percentage REAL,
    effective_date TEXT,
    end_date TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    source_reference TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rel_source ON entity_relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON entity_relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON entity_relationships(relationship_type);
"""

MIGRATION_V3 = """
-- Research Briefs Table (Evidence-grounded company research briefs)
CREATE TABLE IF NOT EXISTS research_briefs (
    research_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    brief_title TEXT NOT NULL,
    executive_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    claims_json TEXT NOT NULL DEFAULT '[]',
    financial_trends_json TEXT NOT NULL DEFAULT '[]',
    data_gaps_json TEXT NOT NULL DEFAULT '[]',
    risk_indicators_json TEXT NOT NULL DEFAULT '[]',
    cited_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    valuation_summary_json TEXT,
    model_provider TEXT NOT NULL,
    model_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_briefs_entity ON research_briefs(entity_id);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON research_briefs(status);
"""

MIGRATION_V4 = """
-- Discovery Runs (Target discovery and screening execution logs)
CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    criteria_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    results_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_created ON discovery_runs(created_at);

-- Saved Target Lists / Watchlists
CREATE TABLE IF NOT EXISTS target_lists (
    list_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_lists_name ON target_lists(name);

-- Target List Membership
CREATE TABLE IF NOT EXISTS target_list_members (
    list_id TEXT NOT NULL REFERENCES target_lists(list_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'NEW',
    notes TEXT,
    source_run_id TEXT,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (list_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_list_members_entity ON target_list_members(entity_id);
CREATE INDEX IF NOT EXISTS idx_list_members_status ON target_list_members(status);

-- Target List Status History
CREATE TABLE IF NOT EXISTS target_list_history (
    history_id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES target_lists(list_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    note TEXT,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_list_history_list ON target_list_history(list_id);
CREATE INDEX IF NOT EXISTS idx_list_history_entity ON target_list_history(entity_id);
"""

MIGRATION_V5 = """
-- Funding Rounds Table
CREATE TABLE IF NOT EXISTS funding_rounds (
    round_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    round_type TEXT NOT NULL,
    announced_date TEXT,
    closing_date TEXT,
    disclosed_amount REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    amount_kind TEXT NOT NULL DEFAULT 'ROUND_TOTAL',
    pre_money_valuation REAL,
    post_money_valuation REAL,
    instrument_type TEXT,
    lead_investor_ids_json TEXT NOT NULL DEFAULT '[]',
    participant_ids_json TEXT NOT NULL DEFAULT '[]',
    source_reference_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    conflict_notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_funding_company ON funding_rounds(company_id);
CREATE INDEX IF NOT EXISTS idx_funding_date ON funding_rounds(announced_date);
CREATE INDEX IF NOT EXISTS idx_funding_type ON funding_rounds(round_type);

-- Funding Participants Table
CREATE TABLE IF NOT EXISTS funding_participants (
    participant_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES funding_rounds(round_id) ON DELETE CASCADE,
    investor_id TEXT REFERENCES companies(entity_id) ON DELETE SET NULL,
    fund_id TEXT REFERENCES companies(entity_id) ON DELETE SET NULL,
    investor_name TEXT,
    is_lead INTEGER NOT NULL DEFAULT 0,
    investor_type TEXT NOT NULL DEFAULT 'INSTITUTION',
    disclosed_commitment REAL,
    currency TEXT,
    shares_acquired REAL,
    implied_ownership_pct REAL,
    ownership_assumptions TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fp_round ON funding_participants(round_id);
CREATE INDEX IF NOT EXISTS idx_fp_investor ON funding_participants(investor_id);
CREATE INDEX IF NOT EXISTS idx_fp_fund ON funding_participants(fund_id);

-- Transactions & M&A Table
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    transaction_type TEXT NOT NULL,
    acquirer_id TEXT REFERENCES companies(entity_id) ON DELETE SET NULL,
    acquirer_name TEXT,
    target_id TEXT NOT NULL REFERENCES companies(entity_id) ON DELETE CASCADE,
    target_name TEXT,
    seller_id TEXT REFERENCES companies(entity_id) ON DELETE SET NULL,
    seller_name TEXT,
    announced_date TEXT,
    expected_completion_date TEXT,
    actual_completion_date TEXT,
    status TEXT NOT NULL DEFAULT 'ANNOUNCED',
    disclosed_value REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    consideration_type TEXT NOT NULL DEFAULT 'UNDISCLOSED',
    consideration_details_json TEXT NOT NULL DEFAULT '{}',
    assets_or_business_unit TEXT,
    regulatory_approvals TEXT,
    termination_reason TEXT,
    source_reference_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    conflict_notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_acquirer ON transactions(acquirer_id);
CREATE INDEX IF NOT EXISTS idx_tx_target ON transactions(target_id);
CREATE INDEX IF NOT EXISTS idx_tx_seller ON transactions(seller_id);
CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(announced_date);

-- Deal Conflicts & Discrepancies Table
CREATE TABLE IF NOT EXISTS deal_conflicts (
    conflict_id TEXT PRIMARY KEY,
    target_event_id TEXT NOT NULL,
    event_category TEXT NOT NULL,
    field_name TEXT NOT NULL,
    source_a_id TEXT NOT NULL,
    observed_value_a TEXT NOT NULL,
    source_b_id TEXT NOT NULL,
    observed_value_b TEXT NOT NULL,
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
    resolution_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conflicts_target ON deal_conflicts(target_event_id);
"""

MIGRATION_V6 = """
-- Workflow Definitions Table
CREATE TABLE IF NOT EXISTS workflow_definitions (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    required_permissions_json TEXT NOT NULL DEFAULT '[]',
    tasks_schema_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Workflow Execution Runs Table
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    version TEXT NOT NULL,
    state TEXT NOT NULL,
    initiator TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wfruns_wf ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wfruns_state ON workflow_runs(state);
CREATE INDEX IF NOT EXISTS idx_wfruns_created ON workflow_runs(created_at);

-- Task Runs Table
CREATE TABLE IF NOT EXISTS task_runs (
    task_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    task_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    max_retries INTEGER NOT NULL DEFAULT 0,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    inputs_json TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    error_classification TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_taskruns_run ON task_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_taskruns_state ON task_runs(state);

-- Workflow Approvals Table
CREATE TABLE IF NOT EXISTS workflow_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_run ON workflow_approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON workflow_approvals(status);

-- Workflow Audit Events Table (Append-Only)
CREATE TABLE IF NOT EXISTS workflow_audit_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    initiator TEXT NOT NULL,
    agent_or_task TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    is_mutation INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_audit_run ON workflow_audit_events(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON workflow_audit_events(timestamp);

-- Workflow Snapshots Table (For Deal Monitoring & Change Detection)
CREATE TABLE IF NOT EXISTS workflow_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    dataset_key TEXT NOT NULL,
    snapshot_timestamp TEXT NOT NULL,
    record_counts_json TEXT NOT NULL DEFAULT '{}',
    content_hashes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_key ON workflow_snapshots(dataset_key);
"""

MIGRATIONS: List[Tuple[int, str, str]] = [
    (1, "001_initial_schema", MIGRATION_V1),
    (2, "002_entity_relationships", MIGRATION_V2),
    (3, "003_research_briefs", MIGRATION_V3),
    (4, "004_target_discovery_and_lists", MIGRATION_V4),
    (5, "005_funding_and_transactions", MIGRATION_V5),
    (6, "006_workflow_orchestration", MIGRATION_V6),
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Ensure the schema migration tracking table exists and apply pending migrations."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
    applied_versions = {row[0] for row in cursor.fetchall()}

    for version, name, sql in MIGRATIONS:
        if version not in applied_versions:
            logger.info("Applying migration %d: %s", version, name)
            cursor.executescript(sql)
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, now_iso),
            )
            conn.commit()
            logger.info("Migration %d successfully applied.", version)
