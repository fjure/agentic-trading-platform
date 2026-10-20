# DEMO-01: Verträge und fail-closed HTTP-Grenzen

Contract-first artifacts trace to REQ-DEMO-005 and REQ-SETUP-001 through REQ-SETUP-005. New operation IDs are `createDemoRun`, `listDemoRuns`, `getDemoRun`, `getDemoSnapshot`, `getDemoHistory`, `controlDemoRun`, `getIntegrationStatuses`, `saveAlpacaIntegration`, `saveJevIntegration`, `getTradingProfile`, and `updateTradingProfile`.

Demo runs bind exact USD `initialBudget` and strategy (`mock-neutral` or `neutral-baseline`); the public DTO carries `simulationOnly: true`, `traceId`, and UTC `createdAt`/`updatedAt`. Status is `RUNNING|SUCCEEDED|FAILED|PAUSED`. Integration statuses expose `status: UNCONFIGURED|CONFIGURED` and `restartRequired`; writes are schema-marked `writeOnly` with 512-character maximum keys, and save responses contain only status and restart requirement. The profile `budget` is an additional held/reserved exposure cap and does not establish ledger cash.

Validation evidence:

- Redocly CLI 2.62.1: `lint contracts/http/control-api.openapi.yaml` passed. It reported the pre-existing missing info license and unused SupervisorEvent warnings.
- Pinned OpenAPI Generator 7.26.0 via `scripts/check-angular-client.ps1 -WriteClient`, using bundled PowerShell 7: two generated outputs matched across 87 files; generated Angular client and fixture TypeScript compiled.
- `scripts/check-contract-breaking.py --base-ref HEAD`: semantic diff passed with oasdiff 1.33.0; no breaking changes.
- Die anfängliche Pythonpfad-Diagnose ist durch die tatsächlich erfolgreiche integrierte Rootprüfung ersetzt: `scripts/validate-contracts.py` bestanden mit31Operationen,14Schemas,13Schema-/13HTTP-Beispielen und4Problembeispielen.

Integrierter Abschluss 2026-10-18: Nach optionalen posttrade Snapshotfeldern `portfolioCash` und `positions` erneut zwei bytegleiche88-Dateien-Generierungen, TypeScript und Angularbuild bestanden; echter HEAD-Breakingdiff weiterhin erfolgreich. Die lokale API-/Vertragssuite hat394Tests in4,83s bestanden (danach zusätzlicher Snapshot-Grenztest). Manuell implementierte neue Transportrouten bleiben ohne injizierte Ports503 und verwenden dieselbe Auth-/Origin-/Body-/Idempotency-Key-Grenze. Canaryprüfungen belegen redigierte Positiv-/Negativantworten und keine Credential-Rückgabe. Keine Providerrequests oder echten Credentials. Runtimeintegration und echte Browser-Datenbank-Abnahme bleiben den folgenden Tasks zugeordnet.
