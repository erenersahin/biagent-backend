"""
BiAgent SQLite Database Schema

Defines all tables for:
- JIRA ticket cache
- Pipeline tracking
- Step outputs and artifacts
- Session persistence
- Webhook subscriptions
"""

SCHEMA_SQL = """
-- ============================================================
-- JIRA TICKET CACHE
-- ============================================================

-- Tickets table (JIRA cache)
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,           -- JIRA issue ID
    key TEXT UNIQUE NOT NULL,      -- PROJ-123
    summary TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    priority TEXT,
    assignee TEXT,
    project_key TEXT,
    issue_type TEXT DEFAULT 'feature',
    epic_key TEXT,                 -- Parent epic key (e.g., PROJ-100)
    epic_name TEXT,                -- Parent epic summary
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    jira_updated_at TIMESTAMP,     -- When JIRA says it was updated
    local_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT                  -- Full JIRA response for agent access
);

CREATE INDEX IF NOT EXISTS idx_tickets_key ON tickets(key);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(assignee);
CREATE INDEX IF NOT EXISTS idx_tickets_epic ON tickets(epic_key);

-- Sync status tracking
CREATE TABLE IF NOT EXISTS sync_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    last_sync_at TIMESTAMP,
    sync_type TEXT,                -- 'initial', 'auto', 'webhook', 'manual'
    tickets_updated INTEGER,
    error TEXT
);

-- Related tickets (links)
CREATE TABLE IF NOT EXISTS ticket_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    link_type TEXT,                -- 'blocks', 'is blocked by', 'relates to'
    FOREIGN KEY (source_key) REFERENCES tickets(key)
);

CREATE INDEX IF NOT EXISTS idx_ticket_links_source ON ticket_links(source_key);

-- ============================================================
-- PIPELINE TRACKING
-- ============================================================

-- Pipeline tracking
CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    cycle_type TEXT DEFAULT 'backend',       -- 'backend', 'frontend', 'fullstack', 'spike', 'oncall_bug'
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'paused', 'completed', 'failed', 'waiting_for_review', 'suspended', 'needs_user_input'
    current_step INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    paused_at TIMESTAMP,
    completed_at TIMESTAMP,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0,
    pause_requested BOOLEAN DEFAULT FALSE,
    claude_session_id TEXT,                  -- Session ID from ClaudeSDKClient for persistence
    session_state_json TEXT,                 -- Serialized session state for reconstruction
    FOREIGN KEY (ticket_key) REFERENCES tickets(key)
);

CREATE INDEX IF NOT EXISTS idx_pipelines_ticket ON pipelines(ticket_key);
CREATE INDEX IF NOT EXISTS idx_pipelines_status ON pipelines(status);

-- Individual step tracking
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'paused', 'completed', 'failed', 'skipped', 'waiting'
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    tokens_used INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    last_feedback_id TEXT,
    waiting_for TEXT,              -- 'github_webhook', etc.
    iteration_count INTEGER DEFAULT 0,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_steps_pipeline ON pipeline_steps(pipeline_id);

-- Step outputs (artifacts)
CREATE TABLE IF NOT EXISTS step_outputs (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    output_type TEXT NOT NULL,      -- 'context', 'risks', 'plan', 'code', 'tests', 'docs', 'pr', 'review'
    content TEXT,
    content_json TEXT,              -- Structured output
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_step_outputs_step ON step_outputs(step_id);

-- Agent state (for pause/resume)
CREATE TABLE IF NOT EXISTS agent_state (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    state_json TEXT NOT NULL,       -- Serialized agent state
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

-- ============================================================
-- TOOL CALLS AND FILE CHANGES
-- ============================================================

-- Tool call logging
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_use_id TEXT,             -- SDK tool_use_id for linking subagent calls
    arguments TEXT,               -- JSON
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_step ON tool_calls(step_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_use_id ON tool_calls(tool_use_id);

-- Subagent tool call logging (real-time capture from SDK streaming)
CREATE TABLE IF NOT EXISTS subagent_tool_calls (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    parent_tool_use_id TEXT NOT NULL,  -- Links to Task's tool_use_id in tool_calls
    tool_use_id TEXT NOT NULL,         -- This subagent tool's SDK ID
    tool_name TEXT NOT NULL,
    arguments TEXT,                     -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_subagent_tc_parent ON subagent_tool_calls(parent_tool_use_id);
CREATE INDEX IF NOT EXISTS idx_subagent_tc_step ON subagent_tool_calls(step_id);
CREATE INDEX IF NOT EXISTS idx_subagent_tc_pipeline ON subagent_tool_calls(pipeline_id);

-- File changes tracking
CREATE TABLE IF NOT EXISTS file_changes (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    change_type TEXT NOT NULL,    -- 'created', 'modified', 'deleted'
    content_before TEXT,
    content_after TEXT,
    diff TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_file_changes_step ON file_changes(step_id);

-- Step artifacts (diffs, outputs)
CREATE TABLE IF NOT EXISTS step_artifacts (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,  -- 'diff', 'test_results', 'pr_description'
    content TEXT,
    content_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_step_artifacts_step ON step_artifacts(step_id);

-- ============================================================
-- USER FEEDBACK
-- ============================================================

-- Step feedback history
CREATE TABLE IF NOT EXISTS step_feedback (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_step_feedback_step ON step_feedback(step_id);

-- Step output history (for tracking revisions)
CREATE TABLE IF NOT EXISTS step_output_history (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    output_type TEXT NOT NULL,
    content TEXT,
    content_json TEXT,
    feedback_id TEXT,              -- Which feedback triggered this revision
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id),
    FOREIGN KEY (feedback_id) REFERENCES step_feedback(id)
);

-- ============================================================
-- CODE REVIEW / PR TRACKING
-- ============================================================

-- Pull request tracking
CREATE TABLE IF NOT EXISTS pull_requests (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    pr_url TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT DEFAULT 'open',        -- 'open', 'approved', 'merged', 'closed'
    approval_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    merged_at TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_pull_requests_pipeline ON pull_requests(pipeline_id);

-- Review comments received
CREATE TABLE IF NOT EXISTS review_comments (
    id TEXT PRIMARY KEY,
    pr_id TEXT NOT NULL,
    github_comment_id TEXT,
    comment_body TEXT NOT NULL,
    file_path TEXT,
    line_number INTEGER,
    reviewer TEXT,
    review_state TEXT,                  -- 'comment', 'approve', 'changes_requested'
    processed BOOLEAN DEFAULT FALSE,
    agent_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    FOREIGN KEY (pr_id) REFERENCES pull_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_review_comments_pr ON review_comments(pr_id);

-- Webhook subscriptions (what we're listening for)
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,        -- 'pull_request', 'jira_ticket'
    resource_id TEXT NOT NULL,          -- PR number or ticket key
    pipeline_id TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- Review iterations (each round of feedback)
CREATE TABLE IF NOT EXISTS review_iterations (
    id TEXT PRIMARY KEY,
    pr_id TEXT NOT NULL,
    iteration_number INTEGER NOT NULL,
    comments_received INTEGER,
    comments_addressed INTEGER,
    commit_sha TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pr_id) REFERENCES pull_requests(id)
);

-- ============================================================
-- CLAUDE SDK SESSION PERSISTENCE
-- ============================================================

-- Claude SDK sessions for pipeline context persistence
CREATE TABLE IF NOT EXISTS claude_sessions (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    claude_session_id TEXT,                  -- Session ID from ClaudeSDKClient
    cwd TEXT NOT NULL,                       -- Working directory
    model TEXT DEFAULT 'claude-sonnet-4-20250514',
    status TEXT DEFAULT 'active',            -- 'active', 'paused', 'completed', 'expired'
    conversation_summary TEXT,               -- Summarized context for reconstruction
    last_step_completed INTEGER DEFAULT 0,
    ticket_context_json TEXT,                -- JSON: ticket info for session restoration
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP,
    paused_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_claude_sessions_pipeline ON claude_sessions(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_claude_sessions_status ON claude_sessions(status);

-- ============================================================
-- SESSION PERSISTENCE
-- ============================================================

-- Session tracking
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active_tab TEXT,                -- ticket_key of active tab
    ui_state TEXT                   -- JSON: scroll positions, expanded panels, etc.
);

-- Session tabs (open tickets)
CREATE TABLE IF NOT EXISTS session_tabs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ticket_key TEXT NOT NULL,
    pipeline_id TEXT,
    tab_order INTEGER,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_viewed_at TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_session_tabs_session ON session_tabs(session_id);

-- Token buffer for reconnection
CREATE TABLE IF NOT EXISTS token_buffer (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    tokens TEXT NOT NULL,           -- Buffered token string
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,           -- Auto-delete after 5 min
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- Offline events (what happened while disconnected)
CREATE TABLE IF NOT EXISTS offline_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    pipeline_id TEXT,               -- Pipeline the event belongs to
    event_type TEXT NOT NULL,       -- 'step_completed', 'pipeline_completed', 'pipeline_failed', etc.
    event_data TEXT,                -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_offline_events_session ON offline_events(session_id, acknowledged);
CREATE INDEX IF NOT EXISTS idx_offline_events_pipeline ON offline_events(pipeline_id);

-- ============================================================
-- GIT WORKTREE ISOLATION
-- ============================================================

-- Worktree session for a pipeline (may contain multiple repos)
CREATE TABLE IF NOT EXISTS worktree_sessions (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    ticket_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'creating', 'ready', 'needs_user_input', 'failed', 'cleaned'
    base_path TEXT NOT NULL,                 -- e.g., biagent-worktrees/PROJ-123/
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ready_at TIMESTAMP,
    cleaned_at TIMESTAMP,
    error_message TEXT,
    user_input_request TEXT,                 -- JSON: what input is needed
    user_input_response TEXT,                -- JSON: user's response
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_worktree_sessions_pipeline ON worktree_sessions(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_worktree_sessions_status ON worktree_sessions(status);
CREATE INDEX IF NOT EXISTS idx_worktree_sessions_ticket ON worktree_sessions(ticket_key);

-- Individual repo worktrees within a session
CREATE TABLE IF NOT EXISTS worktree_repos (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    repo_path TEXT NOT NULL,                 -- Path to main repo
    worktree_path TEXT NOT NULL,             -- Path to worktree
    branch_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'creating', 'setup', 'ready', 'failed'
    setup_commands TEXT,                     -- JSON: detected or user-provided commands
    setup_output TEXT,                       -- Output from setup commands
    pr_url TEXT,                             -- PR created for this repo
    pr_merged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ready_at TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES worktree_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_worktree_repos_session ON worktree_repos(session_id);
CREATE INDEX IF NOT EXISTS idx_worktree_repos_branch ON worktree_repos(branch_name);
CREATE INDEX IF NOT EXISTS idx_worktree_repos_pr ON worktree_repos(pr_url);

-- ============================================================
-- CLARIFICATIONS
-- ============================================================

-- Clarification requests from agents
CREATE TABLE IF NOT EXISTS clarifications (
    id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    question TEXT NOT NULL,              -- The question being asked
    options TEXT NOT NULL,               -- JSON array of 2-4 option strings
    selected_option INTEGER,             -- Index of selected option (0-based)
    custom_answer TEXT,                  -- Free text if "Other" selected
    context TEXT,                        -- Why the agent is asking
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'answered'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    answered_at TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_clarifications_step ON clarifications(step_id);
CREATE INDEX IF NOT EXISTS idx_clarifications_pipeline ON clarifications(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_clarifications_status ON clarifications(status);

-- ============================================================
-- SHARE LINKS
-- ============================================================

-- Share links for read-only pipeline viewing
CREATE TABLE IF NOT EXISTS share_links (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,         -- Unique token for the share URL
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,               -- Optional expiration
    view_count INTEGER DEFAULT 0,       -- Track views
    last_viewed_at TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE INDEX IF NOT EXISTS idx_share_links_pipeline ON share_links(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links(token);

-- ============================================================
-- TICKET ATTACHMENTS
-- ============================================================

-- Attachments for tickets (cached from JIRA)
CREATE TABLE IF NOT EXISTS ticket_attachments (
    id TEXT PRIMARY KEY,                 -- JIRA attachment ID
    ticket_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    size INTEGER,                        -- Size in bytes
    content_url TEXT NOT NULL,           -- URL to fetch content from JIRA
    thumbnail_url TEXT,                  -- URL to thumbnail (for images)
    author TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (ticket_key) REFERENCES tickets(key)
);

CREATE INDEX IF NOT EXISTS idx_ticket_attachments_ticket ON ticket_attachments(ticket_key);

-- ============================================================
-- CYCLE TYPES
-- ============================================================

-- Cycle type definitions (backend, frontend, fullstack, spike, oncall_bug)
CREATE TABLE IF NOT EXISTS cycle_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,           -- 'backend', 'frontend', 'fullstack', 'spike', 'oncall_bug'
    display_name TEXT NOT NULL,          -- 'Backend', 'Frontend', etc.
    description TEXT,
    icon TEXT,                           -- Optional icon identifier
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cycle phases for each cycle type
CREATE TABLE IF NOT EXISTS cycle_phases (
    id TEXT PRIMARY KEY,
    cycle_type_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,        -- 1-8
    name TEXT NOT NULL,                  -- 'Context', 'Risk', etc.
    description TEXT,
    is_enabled BOOLEAN DEFAULT TRUE,     -- Whether this phase is active for this cycle type
    FOREIGN KEY (cycle_type_id) REFERENCES cycle_types(id),
    UNIQUE(cycle_type_id, step_number)
);

CREATE INDEX IF NOT EXISTS idx_cycle_phases_type ON cycle_phases(cycle_type_id);

-- ============================================================
-- WAITLIST
-- ============================================================

-- Waitlist signups
CREATE TABLE IF NOT EXISTS waitlist (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    role TEXT,                      -- 'developer', 'lead', 'manager', 'founder', 'other'
    use_cases TEXT,                 -- JSON array of selected use case IDs
    created_at TIMESTAMP NOT NULL   -- UTC timestamp from frontend
);

CREATE INDEX IF NOT EXISTS idx_waitlist_email ON waitlist(email);
CREATE INDEX IF NOT EXISTS idx_waitlist_created_at ON waitlist(created_at);

-- ============================================================
-- TRACKED REPOSITORIES
-- ============================================================

-- Repositories registered for use with BiAgent
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,                  -- Short name (e.g., 'biagent')
    full_name TEXT NOT NULL UNIQUE,      -- Full name (e.g., 'owner/biagent')
    local_path TEXT NOT NULL,            -- Absolute path on disk
    clone_url TEXT,                      -- GitHub clone URL
    ssh_url TEXT,                        -- SSH URL
    html_url TEXT,                       -- Browser URL
    default_branch TEXT DEFAULT 'main',
    language TEXT,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,      -- Whether repo is available for pipelines
    github_id INTEGER,                   -- GitHub repo ID if from GitHub
    setup_commands TEXT,                 -- JSON array of setup commands
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_repositories_full_name ON repositories(full_name);
CREATE INDEX IF NOT EXISTS idx_repositories_active ON repositories(is_active);

-- ============================================================
-- RISK TRACKING
-- ============================================================

-- Risk cards generated by Risk Agent
CREATE TABLE IF NOT EXISTS risk_cards (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    severity TEXT NOT NULL,           -- 'high', 'medium', 'low'
    category TEXT NOT NULL,           -- 'technical', 'security', 'performance', 'dependency', 'testing', 'blocker'
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    impact TEXT,                      -- Description of potential impact
    mitigation TEXT,                  -- Suggested mitigation strategy
    is_blocker BOOLEAN DEFAULT FALSE, -- If true, blocks further progress
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,             -- User who acknowledged
    acknowledged_at TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_risk_cards_pipeline ON risk_cards(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_risk_cards_severity ON risk_cards(severity);
CREATE INDEX IF NOT EXISTS idx_risk_cards_acknowledged ON risk_cards(acknowledged);
CREATE INDEX IF NOT EXISTS idx_risk_cards_blocker ON risk_cards(is_blocker);
"""

# Migrations to run after schema creation (for existing databases)
MIGRATIONS_SQL = """
-- Migration: Add tool_use_id column to tool_calls if it doesn't exist
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we use a workaround
-- We check in Python if the column exists before running this

-- Migration: Create subagent_tool_calls table (already uses IF NOT EXISTS above)
"""

def get_migration_sql(existing_columns: list[str]) -> str:
    """Generate migration SQL based on existing schema state.

    Args:
        existing_columns: List of column names that exist in tool_calls table

    Returns:
        SQL to run for migrations
    """
    migrations = []

    # Add tool_use_id to tool_calls if missing
    if 'tool_use_id' not in existing_columns:
        migrations.append("ALTER TABLE tool_calls ADD COLUMN tool_use_id TEXT;")
        migrations.append("CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_use_id ON tool_calls(tool_use_id);")

    return "\n".join(migrations)
