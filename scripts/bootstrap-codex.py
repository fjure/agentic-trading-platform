"""Reproduce repository skills and secret-free project MCP configuration only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    'api-contract-workflow': (
        'Use when HTTP, SSE, OpenAPI or JSON Schema contracts change; enforce contract-first validation and generated Angular client integrity.',
        '''## Scope and inputs
Apply to API operations, event schemas, DTOs, examples and generated clients. Exclude domain-only edits without contract impact. Read AGENTS.md, the active OpenSpec change, canonical OpenAPI, schemas, examples, pinned validator/generator versions and base-branch contract.

## Workflow
1. Update OpenSpec requirements and scenarios first.
2. Update contracts/http/control-api.openapi.yaml, contracts/schemas/ and contracts/examples/ together.
3. Run the repository's pinned deterministic CLI validators and example checks; stop on errors. Never substitute an OpenAPI MCP.
4. Compare against the base branch for breaking changes. Missing base contracts require an explicit initial-contract record, never a fabricated successful diff.
5. Implement FastAPI transport adapters only in an authorized application implementation run; do not generate a server stub.
6. Generate the Angular client with the pinned CLI, then regenerate and compare for drift. Never edit generated code manually.
7. Run behavior, negative/fuzz and transport contract tests. Link operations and schemas to Requirement, Task and Test IDs.

## Boundaries and evidence
Respect the documentation-only bootstrap phase: record blocked runtime/client checks, do not create application code just to pass them. Provide exact tool versions, commands, validation output, breaking-change result, generator compatibility, drift comparison and traceability. A missing validator, unsupported OAS feature or failed check blocks completion.
'''),
    'trading-safety-review': (
        'Review changes to risk, execution, brokers, operating modes, secrets, orders, reconciliation or deployment for trading safety invariants.',
        '''## Scope and inputs
Apply to the named safety boundaries, including specifications. Exclude unrelated presentation-only edits. Read AGENTS.md, relevant requirements, design decisions, diffs, tests, process permissions and deployment configuration.

## Workflow
Produce a checklist with finding, concrete file/test evidence and blocking deviation for each of: agent isolation from BrokerAdapter submit; deterministic RiskDecision and Execution gates; LIVE unreachable; DISABLED/fail-closed defaults and unsafe restart pause; Decimal and UTC; independent kill-switch/PAUSE_NEW; Execution-only credentials and redaction; immutable proposal/risk references and full audit trace.
Review atomic budget reservation across positions/open orders/reservations, expiry recheck before submit, unknown-status reservation retention, timeout reconciliation before resubmit, partial fills and cancel/fill races. Check that Jev failure cannot disable reconciliation or pause.
Map every money/security invariant to a meaningful test, inspect its result and classify pass, fail or unverified. Document-only review cannot certify absent runtime behavior.

## Boundaries and evidence
Never use broker credentials or real trading endpoints during review. Do not repair safety failures by bypassing gates. Return checklist, requirement-to-test links, commands/results, residual risks and blockers. Any failed or unverified mandatory invariant prevents implementation certification.
'''),
    'broker-adapter-certification': (
        'Certify new or changed BrokerAdapter implementations and capability contracts against FakeBroker, recovery and reconciliation scenarios.',
        '''## Scope and inputs
Apply to adapter behavior, broker capabilities, order mapping and reconnect/recovery changes. Exclude market-data-only changes without broker impact. Read port contract, capability matrix, provider's official API/version, FakeBroker fixtures and requirement/test mapping.

## Workflow
1. Verify capabilities explicitly: order types, quantities, sessions, shorts, brackets and Paper availability. Unsupported/unconfirmed capabilities must reject deterministically.
2. Run the shared FakeBroker contract suite and adapter-specific mapping tests.
3. Exercise idempotency, timeout after possible submit, rate limit/backoff, partial fill, cancel/fill races, unknown status, duplicate/out-of-order events, reconnect and reconciliation after process death.
4. Confirm reservation retention for unknown outcomes and reconciliation by internal/client and broker IDs before any retry. Assert no blind resubmit or double order in simulated recovery.
5. Record API constraints and evidence per capability/scenario; approve only when mandatory negative and recovery cases pass.

## Boundaries and evidence
A happy path alone never certifies an adapter. Normal certification uses FakeBroker and local tests without credentials; Paper smoke is separate, explicit opt-in and hard-guarded to Paper endpoints. Report versions, matrix, commands, results, state-transition evidence and unresolved blockers. No LIVE tests or broker MCP.
'''),
    'decision-engine-evaluation': (
        'Evaluate changes to Jev, prompts, atomic questions, DecisionEngine adapters or decision logic using versioned replay and shadow comparisons.',
        '''## Scope and inputs
Apply to typed outputs, fallback/abstention thresholds and model/question/prompt versions. Exclude risk-only and presentation-only changes. Read immutable normalized input corpus, its hashes, schemas, versioned Jev configuration, MockDecisionEngine and deterministic baseline, requirements and evaluation thresholds.

## Workflow
1. Freeze corpus, clock, features and evidence availability times; reject future information leakage.
2. Replay identical inputs against Mock and deterministic baseline and each versioned Jev configuration. Replay has no BrokerAdapter submission access.
3. Compare schema compliance, abstention rate, confidence calibration against labeled decisions, latency percentiles, API/token costs when reported, repeated-run stability and input/output version coverage.
4. Inject timeout, invalid schema, missing required fields, stale input and low confidence. Require ABSTAIN/no new exposure and independent risk/reconciliation/pause behavior.
5. Evaluate Shadow separately from actual trading; Shadow never submits orders. Preserve response distributions, model/question/prompt versions, input hash, trace ID, latency, usage and expiry.

## Boundaries and evidence
Confidence is never profit probability or a direct sizing input. Short-term Paper P&L alone cannot authorize promotion. Missing official Jev schema/provider documentation is a blocker, not permission to guess. Return versioned corpus/config hashes, metrics and thresholds, schema/failure tests, comparison results and acceptance decision. No Jev credentials in prompts/logs and no broker calls.
'''),
    'github-issue-sync': (
        'Synchronize changed OpenSpec tasks, requirement mappings, dependencies or task status idempotently to Issues using the official GitHub MCP.',
        '''## Scope and inputs
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
'''),
}

def main():
    for name, (description, body) in SKILLS.items():
        target = ROOT / '.agents' / 'skills' / name / 'SKILL.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise SystemExit(f'Refusing to overwrite existing skill: {name}')
        target.write_text(f'---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}', encoding='utf-8')
    config = ROOT / '.codex' / 'config.toml'
    if config.exists():
        raise SystemExit('Refusing to overwrite existing project Codex configuration')
    config.parent.mkdir(parents=True, exist_ok=True)
    project = ROOT.as_posix()
    config.write_text(f'''# Official project-only MCPs. No secrets. GitHub authentication is external.
[mcp_servers.context7]
command = "node"
args = ["{project}/.tooling/node_modules/@upstash/context7-mcp/dist/index.js"]
enabled_tools = ["resolve-library-id", "query-docs"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 30

[mcp_servers.playwright]
command = "node"
args = ["{project}/.tooling/node_modules/@playwright/mcp/cli.js", "--isolated", "--headless", "--browser", "msedge", "--block-service-workers", "--no-webmcp", "--codegen", "none", "--output-dir", "{project}/.tooling/output"]
enabled_tools = ["browser_navigate", "browser_snapshot", "browser_take_screenshot", "browser_console_messages", "browser_network_requests", "browser_click", "browser_fill_form", "browser_select_option", "browser_press_key", "browser_wait_for", "browser_close", "browser_resize"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 30

[mcp_servers.github]
url = "https://api.githubcopilot.com/mcp/"
bearer_token_env_var = "GITHUB_PAT_TOKEN"
enabled_tools = ["get_me", "get_file_contents", "list_branches", "list_commits", "list_issues", "search_issues", "issue_read", "get_label", "list_label", "list_pull_requests", "pull_request_read", "actions_list", "actions_get", "issue_write", "label_write"]
default_tools_approval_mode = "writes"
http_headers = {{ "X-MCP-Toolsets" = "context,repos,issues,labels,pull_requests,actions", "X-MCP-Features" = "mcp_apps_disable_form_deferral" }}

[mcp_servers.github.tools.issue_write]
approval_mode = "prompt"

[mcp_servers.github.tools.label_write]
approval_mode = "prompt"
''', encoding='utf-8')
    print(f'Created {len(SKILLS)} skills and project MCP configuration.')

if __name__ == '__main__':
    main()
