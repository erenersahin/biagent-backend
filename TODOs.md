# Backend TODOs - Feature Gap Analysis

**Generated:** 2026-01-24
**Based on:** Features 01-07 specification analysis

---

## Summary

| Feature | Status | Completeness |
|---------|--------|--------------|
| 01-JIRA Sync | Partial | 70% |
| 02-Pipeline Execution | Mostly Done | 85% |
| 03-User Feedback | Mostly Done | 80% |
| 04-Code Review | Mostly Done | 75% |
| 05-Agent Execution | Well Done | 85% |
| 06-Session Persistence | Well Done | 80% |
| 07-Worktree Isolation | Well Done | 85% |

---

## Feature 01: JIRA Ticket Sync

### Implemented ✓
- Database schema (tickets, sync_status, ticket_links, ticket_attachments)
- API endpoints (GET/POST /api/tickets/*, sync, stats)
- JIRA sync service with scheduler
- Attachment proxying

### Missing ✗

- [ ] **WebSocket Messages**
  - [ ] `sync_complete` - Broadcast when sync finishes
  - [ ] `ticket_updated` - Broadcast when single ticket changes
  - [ ] `tickets_updated` - Broadcast for batch updates
  - [ ] `sync_error` - Broadcast sync failures
  - [ ] Token streaming for sync progress

- [ ] **JIRA CLI Integration**
  - [ ] `biagent-jira list` command
  - [ ] `biagent-jira get PROJ-123` command
  - [ ] `biagent-jira related PROJ-123` command
  - [ ] `biagent-jira update` command
  - [ ] Typer-based CLI in `cli/` directory

- [ ] **Webhook Improvements**
  - [ ] Test JIRA webhook signature verification
  - [ ] Document `process_jira_webhook()` implementation

---

## Feature 02: Pipeline Execution

### Implemented ✓
- Database schema (pipelines, pipeline_steps, step_outputs, step_artifacts)
- All API endpoints (create, start, pause, resume, restart)
- Agent configuration in config.py
- PipelineEngine class with async execution
- New PipelineSession for context persistence

### Missing ✗

- [ ] **Cycle Type Support** (HIGH PRIORITY)
  - [ ] Add `cycle_type` column to pipelines table
  - [ ] Create `cycle_types` table (spike, backend, frontend, fullstack, oncall_bug)
  - [ ] Create `phase_definitions` table
  - [ ] Create `cycle_phase_config` table
  - [ ] Implement cycle detection service
  - [ ] Dynamic phase loading instead of hardcoded STEP_CONFIGS

- [ ] **Risk Analysis**
  - [ ] Risk severity levels (high/medium/low) in schema
  - [ ] Risk card generation in Risk Agent
  - [ ] Risk acknowledgment tracking

- [ ] **WebSocket Messages** (verify implementation)
  - [ ] `pipeline_started` with cycle_type
  - [ ] `step_restarted` message type
  - [ ] `pipeline_failed` with error details

---

## Feature 03: User Feedback & Clarifications

### Implemented ✓
- Database schema (step_feedback, step_output_history, clarifications)
- API endpoints for feedback and restart
- Clarifications router

### Missing ✗

- [ ] **Clarification Tool**
  - [ ] Agent tool for `request_clarification`
  - [ ] Tool schema validation
  - [ ] Clarification response validation

- [ ] **WebSocket Messages**
  - [ ] `clarification_requested` broadcast
  - [ ] `clarification_answered` broadcast
  - [ ] `step_interrupted` broadcast
  - [ ] `step_restarted` broadcast

- [ ] **Feedback Acknowledgment**
  - [ ] "Revised based on your feedback" indicator in output
  - [ ] Attempt counter in step data

---

## Feature 04: Code Review Response

### Implemented ✓
- Database schema (pull_requests, review_comments, webhook_subscriptions, review_iterations)
- GitHub webhook endpoint with signature verification
- Review agent configuration

### Missing ✗

- [ ] **Review Agent Tools**
  - [ ] `github_cli` tool implementation
  - [ ] PR comment reply functionality
  - [ ] File diff generation
  - [ ] Test execution integration

- [ ] **Webhook Processing**
  - [ ] Comment debouncing (30-second window)
  - [ ] Comment batching logic
  - [ ] Handle "Changes Requested" vs "Approved" states

- [ ] **WebSocket Messages**
  - [ ] `waiting_for_review` broadcast
  - [ ] `review_received` broadcast
  - [ ] `review_responded` broadcast
  - [ ] `pr_approved` broadcast
  - [ ] `changes_requested` broadcast

---

## Feature 05: Agent Step Execution

### Implemented ✓
- Database schema (tool_calls, file_changes, step_artifacts)
- All 8 agent configurations
- BaseAgent, AgentContext, CostTracker classes
- PipelineSession for persistent context
- Token streaming infrastructure

### Missing ✗

- [ ] **Tool Implementations**
  - [ ] Document `file_read` tool handler
  - [ ] Document `file_write` tool handler
  - [ ] Document `bash` tool handler
  - [ ] Implement `jira_cli` tool (see Feature 01)
  - [ ] Implement `github_cli` tool (see Feature 04)
  - [ ] Document `notion_mcp` tool

- [ ] **WebSocket Messages**
  - [ ] `tool_call_started` with tool name and args
  - [ ] `tool_call_completed` with result
  - [ ] `file_changed` with path and change type

---

## Feature 06: Session Persistence

### Implemented ✓
- Database schema (sessions, session_tabs, token_buffer, offline_events, claude_sessions)
- API endpoints (restore, tabs, ui-state, acknowledge-events)
- Session store service with all helpers
- Claude SDK session persistence

### Missing ✗

- [ ] **Offline Events**
  - [ ] Logic to generate offline events during disconnection
  - [ ] Event replay mechanism
  - [ ] Notification system for missed events

- [ ] **Token Buffer**
  - [ ] Buffer flush logic
  - [ ] Buffer expiration cleanup
  - [ ] Reconnection token catchup

- [ ] **WebSocket Messages**
  - [ ] `session_restored` broadcast
  - [ ] `offline_event` broadcast
  - [ ] `token_catchup` broadcast

---

## Feature 07: Worktree Isolation

### Implemented ✓
- Database schema (worktree_sessions, worktree_repos)
- Configuration in config.py
- WorktreeManager service structure
- SetupDetector service
- API endpoints for worktrees
- User input flow for setup commands

### Missing ✗

- [ ] **Worktree Operations** (implementation details)
  - [ ] `create_session()` - git worktree add logic
  - [ ] `run_setup()` - command execution in sandbox
  - [ ] `cleanup_session()` - git worktree remove logic
  - [ ] `check_and_cleanup_merged()` - post-merge cleanup

- [ ] **Multi-Repository API**
  - [ ] `GET /api/repositories` implementation
  - [ ] `POST /api/repositories` implementation
  - [ ] `DELETE /api/repositories/{id}` implementation
  - [ ] `GET /api/tickets/{key}/repositories` implementation

- [ ] **WebSocket Messages**
  - [ ] `worktree_session_creating` broadcast
  - [ ] `worktree_repo_created` broadcast
  - [ ] `worktree_setup_started` broadcast
  - [ ] `worktree_session_ready` broadcast
  - [ ] `pipeline_needs_input` broadcast
  - [ ] `worktree_session_cleaned` broadcast

---

## New SDK Changes (PipelineSession)

### Implemented ✓
- `agents/pipeline_session.py` - ClaudeSDKClient wrapper
- `services/session_store.py` - Session persistence helpers
- Context persistence across pipeline steps
- Pause/resume with context reconstruction

### Frontend Impact
- [ ] No frontend changes required for basic functionality
- [ ] Frontend already handles `worktree_status` and `user_input_request`
- [ ] Session restore includes full pipeline state

---

## Priority Order

### HIGH (Critical Path)
1. WebSocket message implementations (all features)
2. Cycle type support (Feature 02)
3. GitHub comment reply functionality (Feature 04)

### MEDIUM (Important)
4. JIRA CLI commands (Feature 01)
5. Worktree operation implementations (Feature 07)
6. Clarification tool (Feature 03)

### LOW (Nice to Have)
7. Offline event generation (Feature 06)
8. Token buffer management (Feature 06)
9. Tool documentation (Feature 05)
