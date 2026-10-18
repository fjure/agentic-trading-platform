# Proposal

Historischer Bootstrapentwurf mit anschließend gesondert freigegebener Apply-Phase. Die ursprünglichen Aussagen „Jetzt entstehen nur Planung“ und „Kein Commit oder Push“ beschreiben den ersten Durchlauf; der Nutzerauftrag vom2026-10-13 autorisiert inzwischen geprüfte Issue-Branches und reguläre main-Integration nach AGENTS.md. Aktueller Implementierungs-/Abnahmestand: `implementation-evidence.md`, `readiness.md` und `docs/evidence/ptv-23-final-acceptance.md`. Der ursprüngliche fachliche Umfang und alle Trading-/Secretgrenzen bleiben erhalten.

## Why

Das Greenfield-Projekt benötigt einen prüfbaren ersten Datenfluss von normalisierten Marktdaten über Entscheidung und deterministische Risikoprüfung bis zur Paper-Ausführung und Supervisoransicht. Ein vollständig lokaler Fake-Durchstich muss Sicherheits- und Recoverygrenzen beweisen, bevor externe Adapter hinzukommen.

## What Changes

- Spezifikation eines modularen Python-Systems mit getrenntem Execution-Prozess und typisierten externen Ports.
- Unveränderliche Entscheidungsinputs, versionierte Entscheidungen, atomare Risikoreservierungen, Orderledger, Reconciliation, Jobs und Outbox.
- Control API, interne JSON-Schemas, Beispiele, Angular-Material-Dashboard mit Snapshot/Cursor/SSE und lokale Betriebsdokumentation.
- Kleine abhängigkeitsgeordnete Implementierungstasks, Traceability, Teststrategie und idempotente GitHub-Issue-Zuordnung.
- Der initiale Durchlauf erstellt ausschließlich Planung, Contracts und Entwicklungsprüfwerkzeuge. Die anschließend gesondert freigegebene Apply-Phase implementiert die offenen Anwendungstasks unter denselben Sicherheitsgrenzen.

## Capabilities

### New Capabilities

- `market-evidence`: normalisierte abgeschlossene Kerzen, Datenqualität, Sessions, neutrale Evidenz und Fähigkeiten.
- `decision-replay`: austauschbare Entscheidungen, Enthaltung, atomare Jev-Fragen und reproduzierbare Replayinputs.
- `risk-execution`: Betriebsmodi, deterministische Freigabe, Budgetreservierung, Orders und Reconciliation.
- `durable-runtime`: Persistenz, Leases, Outbox, Audit, Observability und Secretgrenzen.
- `supervisor-control`: HTTP-Vertrag, sichere Kontrollaktionen, Dashboard und lückenfreier Reconnect.

### Modified Capabilities

Keine; das Projekt besitzt noch keine aktiven Spezifikationen.

## Impact

Geplante Pfade: `packages/`, `services/`, `apps/dashboard/`, `config/`, `infra/compose/`, `tests/`. Jetzt entstehen nur `openspec/changes/paper-trading-vertical-slice/`, `contracts/`, `docs/` und Prüftooling unter `scripts/` beziehungsweise `.tooling/`.
Nur PAPER mit LONG/FLAT, 15-Minuten-Kerzen und kleinem konfigurierbarem Universum. LIVE, Shorts, Hebel, Optionen, Futures, Geldbewegungen, produktive News/Paper-Ingestion, autonome Strategiepromotion, vollständige drei Agentenworkflows, historische Walk-forward-Pipeline, IBKR und CCXT bleiben späteren Changes vorbehalten. Kein Commit oder Push; externe Schreiboperationen ausschließlich autorisierter Task-Issue-/Label-Sync auf `fjure/agentic-trading-platform`.
