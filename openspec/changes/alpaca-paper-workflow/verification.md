# Traceability, Prüfstrategie und Ready-Gates

Stand20.10.2026, ausschließlich Planung. Kein Eintrag hier zertifiziert eine noch nicht implementierte Paperfunktion.

| Requirement | Szenarien | Verantwortliche Tasks | Wesentliche Evidenz |
|---|---|---|---|
| REQ-APW-001 | T-APW-001 | APW-01,09,10 | Externer offizieller Einstieg, kein Eröffnungsrequest/PII |
| REQ-APW-002 | T-APW-002,003 | APW-01,03,09,10 | Executionprüfungen, UUID/USD/Sperren/Feed, redigierter Zustand |
| REQ-APW-003 | T-APW-004 | APW-02,03,09,10 | Mounts/Canaries/Rotation/Generation bei UNKNOWN |
| REQ-APW-004 | T-APW-005,006 | APW-02,04,10 | Tatsächliche SQLrechte, konsistente einmalige Basis, Normalledgerhash |
| REQ-APW-005 | T-APW-007,008 | APW-01,02,03,05,09,10 | Dauerhafte Aktivierung/Idempotenz/TTL/Restartpause |
| REQ-APW-006 | T-APW-009,010 | APW-01,06,07,10 | LIMIT vor Persistierung, Decimal/Version/Riskbudget |
| REQ-APW-007 | T-APW-011,012 | APW-01,06,07,09,10 | Menschliche Provenienz, Doubleclick, konkurrierende Budgetreservation |
| REQ-APW-008 | T-APW-013,014 | APW-05,08,09,10 | Auditiertes Cancel, Killpriorität, Fillrace/keine Liquidation |
| REQ-APW-009 | T-APW-015,016 | APW-04,05,06,08,10 | Crash/UNKNOWN/IDlookup/Fees/Backoff/keine Blindorder |
| REQ-APW-010 | T-APW-017,018 | APW-01,02,09,10 | Getrennte echte UIketten und Reload/Authverlust |
| REQ-APW-011 | T-APW-019,020 | APW-10 | Offlinezertifizierung und ausdrückliche menschliche Providergrenze |

## Prüfaufbau

Domain-/Propertytests für Dezimalgrenzen, Tick/Quantisierung, TTL, Quelle/Version, Long-/Budgetgrenzen. Gemeinsame FakeBroker-/Alpacaadaptertests für jede zertifizierte Capability und sichere Ablehnung. Tatsächliches PostgreSQL für einmalige Basis, Rollen, atomare Reservierungen, Fencing, Outbox/Audit und Crashwiederaufnahme. Tatsächliche Deploymentprüfungen für getrennte Paper-/Fake-/Demomounts, Socket-/Netzrechte und niemals Docker-Socket.

Abschließendes Chromium spricht die gebaute Angularoberfläche und tatsächliche API, Worker/Execution, Migrationen und SQL an. Nur der äußere Alpaca-Transport ist ein lokales deterministisches Providerdouble mit Submitjournal und Fehlersteuerung; kein HTTPmock ersetzt die eigene API. Testfälle: erfolgreiche Bindung mit echter Basis,100USD Budget, zulässige Order oder ehrliche Capabilityablehnung, Teilfill, Vollfill, Cancelrace, Crash nach Annahme, Rate-Limit, Identitätswechsel, externe Aktivität, gestörter Feed, Restartpause, Kill und unveränderte Fake-/Demobestände. Kein echter Schlüssel, keine externen Providerhosts oder Orders. Browserartefakte mit potentiellen Eingaben bleiben deaktiviert.

## Ready vor Apply

- Nutzerumfang geklärt: persönliche Kontoeröffnung extern; danach sämtliche normalen Schritte in der Anwendung. USD und vorhandene Mock-/Baselinevarianten aus vorigem Auftrag bleiben gültig.
- Proposal, elf Requirements/20Szenarien, Design mit sechs Entscheidungen, zehn abhängigkeitsgeordnete Tasks, aktuelle offizielle Quellen und Traceability liegen vor.
- Striktes OpenSpec und vollständiger idempotenter Issue-Read-back müssen im Planungsdurchlauf tatsächlich bestehen. Ergebnis wird im Toolingstatus dokumentiert, nicht vorweggenommen.
- Kanonischer Vertrag, neue öffentliche Schemas/Beispiele und Domainprovenienz werden in APW-01 contract-first umgesetzt und geprüft, bevor die darauf aufbauenden Anwendungstasks starten. Die aktuelle Planung ändert keinen kanonischen Laufzeitvertrag.
- Gesonderter Apply-Auftrag nach Vorlage der Planung ist nach AGENTS.md und Propose-Skill erforderlich. Das ist der ausstehende Prozessschritt; keine echte Providerfreigabe oder Orderausführung durch Agenten folgt daraus.

## Done

Alle zehn Tasks vollständig implementiert; jeweilige Fachtests/Runbooks im selben Task; sämtliche Pflichtszenarien bestanden, keine unbestätigte finanzielle/Securityinvariante. Additiver tatsächlicher Contractdiff, gültige Beispiele, zweimal bytegleicher Client und Angularbuild. Unabhängiger Safety-/Verifyreview, vollständige Issuezuordnung ohne Drift, pro Issue geprüfter Branch und Merge, erfolgreiche main-CI, finale redigierte Evidenz. Tatsächlicher Alpaca-Lauf bleibt getrennte private menschliche Abnahme und wird nicht als durch lokale Tests bewiesen dargestellt.
