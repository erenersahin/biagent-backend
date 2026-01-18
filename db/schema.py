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
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'paused', 'completed', 'failed', 'waiting_for_review', 'suspended'
    current_step INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    paused_at TIMESTAMP,
    completed_at TIMESTAMP,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0,
    pause_requested BOOLEAN DEFAULT FALSE,
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
    arguments TEXT,               -- JSON
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (step_id) REFERENCES pipeline_steps(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_step ON tool_calls(step_id);

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
    event_type TEXT NOT NULL,       -- 'step_completed', 'pipeline_completed', 'error'
    event_data TEXT,                -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

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
"""
