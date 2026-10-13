---
name: api-contract-workflow
description: Use when HTTP, SSE, OpenAPI or JSON Schema contracts change; enforce contract-first validation and generated Angular client integrity.
---

# api-contract-workflow

## Scope and inputs
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
