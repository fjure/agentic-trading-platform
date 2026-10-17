# Test Strategy

## Status und Gates

Dies ist ein **Testplan**, kein Ausführungsbericht. In diesem Planungsdurchlauf existiert keine Anwendung; alle unten genannten Testdateien sind künftige Zielpfade. Die Definition of Ready verlangt valide Specs, Verträge, Beispiele und vollständige Zuordnung. Die spätere Runtime-Zertifizierung verlangt zusätzlich grüne Tests, Recovery-Evidenz und eine unabhängige Sicherheitsprüfung. Kein Test darf einen Live-Endpunkt erreichen. Normale CI nutzt ausschließlich MockDecisionEngine, Replay und FakeBroker. Ein echter Paper-Smoke ist optional, menschlich ausgelöst und aus normaler CI ausgeschlossen.

Die Pyramide hat fünf Ebenen: Unit/Property für Zahlen, Zeit und Zustandsregeln; Contract/Schema für API, Events und Adapter; Integration für Transaktionen, Jobs und Dashboard; Recovery/Failure Injection für Prozess- und Dienstausfälle; Security für Prozessrechte, Secrets und lokale Kontrollgrenzen. Jeder Test speichert Requirement-ID, Fixture-/Konfigversion und Ergebnis in der CI-Evidenz. Die Zuordnung unten ist vollständig für die 40 Spec-Szenarien.

## Requirement-Test-Matrix

| Test-ID | Ebene und künftiger konkreter Testpfad | Messbarer Kernnachweis |
|---|---|---|
| T-MKT-001 | Contract: `tests/contracts/test_market_data_contract.py` | Fremdes Instrument/Intervall: 0 angenommene Bars |
| T-MKT-002 | Unit: `tests/unit/test_market_bars.py` | Offene/korrigierte Bar: 0 still mutierte Inputs |
| T-MKT-003 | Property: `tests/property/test_decimal_time.py` | NaN/Infinity/Überlauf/Skalenverlust abgelehnt; UTC round-trip |
| T-MKT-004 | Property: `tests/property/test_availability.py` | Für jeden Input gilt `availableAt <= asOf` und Barende `<= asOf` |
| T-MKT-005 | Unit: `tests/unit/test_market_quality.py` | Lücke/Duplikat/Widerspruch ergibt keine freigabefähige Entscheidung |
| T-MKT-006 | Unit: `tests/unit/test_market_sessions.py` | Feiertag, Halbtag und beide DST-Wechsel: Aktien zu, Krypto nach bestätigter Capability offen |
| T-MKT-007 | Contract: `tests/contracts/test_evidence_schema.py` | Neutrales Bündel enthält Herkunft/Konflikt, keine Orderfelder |
| T-MKT-008 | Contract: `tests/contracts/test_market_capabilities.py` | Unbestätigte Intervalle/Assets/News sind nicht verfügbar |
| T-DEC-001 | Integration: `tests/integration/test_decision_inputs.py` | Korrektur erzeugt neuen Hash; alter Input bytegleich |
| T-DEC-002 | Contract: `tests/contracts/test_decision_schema.py` | SHORT/unerlaubte Choice wird zu keiner Proposal-Freigabe |
| T-DEC-003 | Contract: `tests/contracts/test_jev_mapping.py` | Choice/Score/Noul/Confidence getrennt; Sizingfeld ohne Wirkung |
| T-DEC-004 | Unit: `tests/unit/test_decision_fallback.py` | Timeout, Schemafehler, fehlendes Feld, stale, niedrige Confidence: 100 % ABSTAIN |
| T-DEC-005 | Contract: `tests/contracts/test_atomic_questions.py` | Mehrdeutige Antwort abgelehnt; getrennte Frageversionen |
| T-DEC-006 | Integration: `tests/integration/test_decision_versions.py` | Modell/Prompt/Frage/Inputhash/Verteilung/Trace/TTL erhalten |
| T-DEC-007 | Recovery: `tests/recovery/test_replay.py` | Mock/Baseline bei identischen Inputs bytegleich; 0 Broker-Submits |
| T-DEC-008 | Contract: `tests/contracts/test_decision_capabilities.py` | Nicht unterstützter Input/Ausfall führt zu ABSTAIN |
| T-RSK-001 | Unit: `tests/unit/test_modes.py` | DISABLED-Default; LIVE-Config abgelehnt; REPLAY/SHADOW: 0 Submits |
| T-RSK-002 | Recovery: `tests/recovery/test_unsafe_restart.py` | Restart bleibt PAUSE_NEW bis Health und Reconciliation grün |
| T-RSK-003 | Contract: `tests/contracts/test_broker_capabilities.py` | Short/Hebel/unbestätigte Orderart/Fractional/Session abgelehnt |
| T-RSK-004 | Unit: `tests/unit/test_risk_decision.py` | Gleicher Snapshot/Policy ergibt gleiche Decision; Pflichtlücke fail-closed |
| T-RSK-005 | Recovery: `tests/recovery/test_submit_gate.py` | TTL-, Policy-, Pause-, Session- oder Datenwechsel vor Submit: 0 Submits |
| T-RSK-006 | Property: `tests/property/test_risk_limits.py` | Grenzwerte 1 Position/Asset, 2 gesamt, 10 % Notional, 1 % Tagesverlust; exakte Decimalgrenzen |
| T-RSK-007 | Integration: `tests/integration/test_parallel_reservations.py` | Zwei konkurrierende, zusammen zu große Vorschläge: höchstens 1 Erfolg; Budget nie negativ |
| T-RSK-008 | Security: `tests/security/test_execution_isolation.py` | Agent/API/Watchdog ohne Submit-Port/Key; Paper-Smoke ohne Opt-in: 0 Requests |
| T-RSK-009 | Recovery: `tests/recovery/test_unknown_reconcile.py` | Timeout/Crash: 1 stabile clientOrderId, Reservation gehalten, 0 Blind-Retries |
| T-RSK-010 | Property: `tests/property/test_order_state_machine.py` | Alle Übergänge; Teilfüllung/Cancel-Fill in beiden Reihenfolgen, Fillmenge monoton, nie doppelt |
| T-RUN-001 | Integration: `tests/integration/test_ledger_restore.py` | Nach DB-Restart vollständige Proposal→Risk→Intent→Report-Kette |
| T-RUN-002 | Recovery: `tests/recovery/test_duplicate_events.py` | Doppelte/ungeordnete Events: genau 1 wirtschaftliches Delta |
| T-RUN-003 | Integration: `tests/integration/test_job_leases.py` | Leaseverlust/Fencing: alter Worker 0 Commits; Retrylimit/Dead Letter sichtbar |
| T-RUN-004 | Recovery: `tests/recovery/test_outbox_crash.py` | Crash vor Publish: Event nach Restart vorhanden, Consumerwirkung einmalig |
| T-RUN-005 | Security: `tests/security/test_audit_redaction.py` | Jede Mutation hat Trace/Akteur/Reason/UTC; keine Canaries in Audit/Logs |
| T-RUN-006 | Security: `tests/security/test_secret_boundaries.py` | Paper-Key nur Execution; Promptinjection löst 0 Tools/Installationen aus |
| T-RUN-007 | Integration: `tests/integration/test_health_fail_closed.py` | Datenstillstand, DB/Broker/Jev-Ausfall separat sichtbar; neue Einstiege gesperrt |
| T-CTL-001 | Contract: `tests/contracts/test_control_api.py` | OpenAPI-Beispiele, Decimalstrings, UTC, interne DTOs; keine Vendorpayloads |
| T-CTL-002 | Contract: `tests/contracts/test_control_api.py` | Idempotency-Key/Payloadhash, 412/428, traceparent; Wiederholung ohne Zweitwirkung |
| T-CTL-003 | Contract: `tests/contracts/test_control_api.py` | RFC 9457 und stabile Codes für Reject/Conflict/Outage/UNKNOWN; Negativ-/Fuzzfälle |
| T-CTL-004 | Integration: `tests/integration/test_sse_cursor.py` | Snapshot-Cursor atomar; Drop/Duplikat/Ablauf: keine stille Ereignislücke |
| T-CTL-005 | Recovery: `tests/integration/test_pause_resume.py` | Jev/UI tot: CLI/Watchdog-Pause greift; in-flight sichtbar; Reconcile läuft |
| T-CTL-006 | E2E: `tests/e2e/supervisor.spec.ts` | Synthetische schema-validierte Trace-Kette mit Markt, Entscheidung/Verteilung, Vorschlag, Risiko, Teilfüllung und Audit vollständig sichtbar; echte Prozessintegration folgt PTV-21/23 |
| T-CTL-007 | Security: `tests/security/test_control_auth.py` | Loopback/Host/Origin/CSRF, keine Browsersecrets oder Client-Riskentscheidung |

## Zusätzliche Querschnittsnachweise

Freigegebene SQL-Paperintegration vom 2026-10-17, vor PTV-21:

| Nachweis | Zielpfad | Akzeptanz |
|---|---|---|
| T-RSK-024 / T-RUN-024 | `tests/integration/test_alpaca_execution.py`, `tests/unit/test_broker_binding.py` | Tatsächlicher SQL-SUBMITTING-Commit vor MockTransport-I/O, Konto/Asset/Capability/Session/Fencing, Restore nach Crash und UNKNOWN/404 ohne zweiten POST, gehaltene Reservierung, Fake-Regressionsparität |
| T-RSK-025 / T-RUN-025 | `tests/integration/test_alpaca_reconciliation.py`, `tests/contracts/test_alpaca_activities.py` | Vollständige Pagination, Teilfill/Cancel/Restart/Dubletten, gebundene Gebührenautorität, keine unbekannte Nullgebühr, keine vorzeitige Budgetfreigabe; vollständige Bestands-/Kontobelege vor Recovery |

Normale CI und Agentenprüfungen bleiben vollständig offline bezüglich Brokern/Jev. Ein lokales HTTPX-MockTransport-Szenario ist keine echte Paperorder. Die späteren Deploymentchecks benötigen getrennte Prozesse und tatsächliche Rollen-/Mount-/Egress-Negativtests; vorhandene Klassenprüfungen ersetzen diese nicht.

- **Verträge:** `tests/contracts/test_openapi_examples.py`, `test_json_schema_examples.py`, `test_generated_client_drift.py` und `test_breaking_change.py` validieren/linten OpenAPI und Schemas, erzeugen den Angular-Client reproduzierbar und vergleichen gegen eine reale Baseline. Beim Initialvertrag wird die fehlende Main-Baseline ausdrücklich als Initialzustand dokumentiert. Negativ- und Fuzztests prüfen ungültige Dezimalstrings, Zeitstempel, Enumerationen, Missing Fields und Größenlimits. Ein ungültiges Beispiel oder Clientdrift blockiert die spätere Abnahme.
- **Jev-Evaluation:** `tests/evaluation/test_decision_dataset.py` nutzt mindestens 100 feste Fälle einschließlich stale, widersprüchlich und unvollständig. Akzeptierte Antworten erfüllen das Schema zu 100 %; Pflichtfehler führen zu 100 % Enthaltung. Berichtet werden Abstention-Rate, Kalibrierung gegen Eignungslabels, p95-Latenz, Kosten und Stabilität über drei Läufe. Confidence ist keine Gewinnwahrscheinlichkeit; Paper-P&L allein ist kein Freigabekriterium.
- **Brokerzertifizierung:** `tests/contracts/test_broker_adapter.py` prüft FakeBroker und später den Paper-Adapter gegen dieselben Fähigkeiten und Fehlerfälle: Timeout, Rate Limit, Partial Fill, Cancel/Fill, UNKNOWN, Reconnect, Reconciliation. Ein erfolgreicher Happy Path reicht nicht.
- **Recovery-Messung:** In `tests/recovery/test_submit_crash_matrix.py` werden Crashes (1) vor persistiertem SUBMITTING, (2) nach Commit vor Netzwerk, (3) nach Brokerannahme vor lokalem Ack und (4) nach Ack vor Outbox-Publish injiziert. Für jeden Fall: höchstens eine brokerseitige Order pro Intent, keine blinde Zweitsendung, eindeutiger Ledger-/UNKNOWN-Zustand und Reconciliation vor Resume. Die dritte Phase bleibt UNKNOWN, falls Brokerabfragen keinen Beweis liefern.
- **Fehlergrenzen:** `tests/recovery/test_failure_matrix.py` injiziert DB-Neustart, Broker- und Jev-Ausfall, Datenstillstand sowie Watchdog-Heartbeatverlust. Jeder unsichere Zustand sperrt neue Einstiege; Pause, Exitüberwachung und Reconciliation bleiben soweit technisch möglich unabhängig von Jev.
- **Redaction-Canary:** `tests/security/test_redaction_canary.py` verwendet ausschließlich synthetische Canary-Werte in Secret-Mount, Brokerfehler und Evidenztext. CI scannt Logs, Audit, HTTP/SSE, Browserzustand und Artefakte auf exakte Canary-Bytes; Trefferzahl muss 0 sein.
- **Optionaler Paper-Smoke:** `tests/paper/test_opt_in_smoke.py` ist nie Teil normaler CI. Er verlangt menschliche Freigabe, explizites Paper-Flag, verifizierten Paper-Host und Konto, minimale simulierte Order sowie Status und Storno oder Fill. Fehlt irgendein Guard, muss er vor Netz-Submit abbrechen. Runbook: `docs/runbooks/paper-smoke.md`.

## Abnahme-Evidenz

Ein späterer Apply liefert Testreport mit Fixture-/Policy-/Contractversionen, Exitcode, Timestamp UTC, anonymisierter Trace-ID und Link auf redigierte Logs. Blockierend sind jede Doppelorder, jede stille SSE-Lücke, jede Budgetüberschreitung, jedes Secret-Canary-Leck, ein erreichbarer LIVE-Pfad, fehlende Riskreferenz, verlorene Fills, fehlende Reconciliation vor Retry und jeder Vertrag- oder Clientdrift. Erst nach vollständiger Runtime-Evidenz darf die Implementierung als sicher zertifiziert werden.

PTV-18 ergänzt tatsächliche HTTP-/Sitzungstests sowie `tests/integration/test_control_http.py` (durable Replaybelege, ETags, eingeschränkte Rolle, Parallelität, transaktionaler Rollback und Initialisierung ohne Policy). `tests/contracts/test_control_events.py` prüft typisierte Control-Ereignisse und fehlende Policy-Metadaten ausschließlich für Notfall-Latches. Ausführungsevidenz wird separat in docs/evidence/ptv-18-control-api.md aufgezeichnet; die generierte Matrix bleibt die Testzuordnung, kein pauschaler Runtime-Pass.

PTV-19 prüft den atomaren Snapshot bei parallelem Pause-/Publish-Commit, erneute Services mit demselben persistierten Cursor, unveränderte Seitenmitgliedschaft bei parallelen Inserts und Heartbeats, Cursorablauf sowie konkurrierende Outbox-Publisher gegen PostgreSQL. Endliche ASGI-/Generatorprüfungen in tests/contracts/test_supervisor_transport.py decken Last-Event-ID, Heartbeat, Reset/Close und Sessionwiderruf ab; tests/contracts/test_supervisor_projections.py und test_supervisor_events.py prüfen normalisierte Payloads. Laufzeitevidenz separat in docs/evidence/ptv-19-snapshot-sse.md.
