---
name: github-issue-sync
description: Synchronize changed OpenSpec tasks, requirement mappings, dependencies or task status idempotently to Issues using the official GitHub MCP.
---

# github-issue-sync

## Scope and inputs
Apply after tasks.md, scope, acceptance, dependencies or status changes and before Apply completion. Exclude unrelated docs edits. Read verified origin, active proposal/design/specs/tasks, docs/development/github-issue-sync.json, available official GitHub MCP tools, permissions and approval policy.

## Workflow
1. Verify the target is fjure/agentic-trading-platform and authenticate the official GitHub MCP; missing target/auth/permissions blocks synchronization. Never extract credentials or substitute gh/REST for MCP mutations.
2. Enumerate implementable leaf tasks and validate stable IDs, Requirement IDs, concrete target paths, acyclic dependencies, behavior, acceptance, test evidence and Definition of Done.
3. Search ALL relevant open and closed issues by persisted mapping, stable task ID and marker <!-- openspec-task-id: TASK-ID -->. Zero matches permits creation; one permits update; ambiguous, deleted or conflicting matches block without destructive repair.
4. Preview exact title [TASK-ID] short title and managed block delimited by <!-- openspec-managed:start --> / <!-- openspec-managed:end -->. Include change/task/requirements/paths/dependencies/behavior/acceptance/tests/DoD. Preserve every byte outside the block and preserve manual labels; never modify comments.
5. Use openspec and change:paper-trading-vertical-slice labels plus relevant existing component/status labels. Create missing labels only with approved label_write create; never delete labels. Use an existing supported milestone if safe, otherwise document change-label-only operation.
6. Perform authorized issue_write create/update operations under configured approval rules, only on issues (the tool can also mutate PRs: that use is forbidden). Resolve dependencies in a second update pass using linked issue numbers and a machine-readable Depends on list; do not claim native dependencies.
7. Close only when the SAME OpenSpec work state marks the task done, required evidence exists and tests passed. A GitHub close never marks an OpenSpec task done.
8. Read back every issue via MCP; verify total count, unique IDs, markers, title, labels, managed contents, mappings and dependencies. Persist schemaVersion, repository, changeId and taskId/issueNumber/issueUrl/contentHash/syncedAt for each verified task. Hash UTF-8 managed contents with SHA-256 and use UTC time. No token storage.

## Boundaries and evidence
The original bootstrap prompt authorizes this issue sync, not other repository mutations. Preserve approvals; stop on drift/auth failure/ambiguous identity or missing narrow tool access. Return created/updated/unchanged counts, read-back evidence, mapping diff and blockers. OpenSpec remains the scope source; Issue drift blocks Definition of Done.
