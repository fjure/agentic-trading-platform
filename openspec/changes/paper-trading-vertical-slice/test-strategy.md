# Test Strategy

PTV-21 ergänzt die bestehenden Test-IDs um reale Deploymentnachweise: T-RUN-003/005/006/007 und T-CTL-005/007 in `tests/integration/test_failures.py` (Jev-/Daten-/Broker-/DBausfall, Fencing, unabhängige Pause/Reconcile/Watchdog, tatsächliche Rollen, SQL-Metriken und parallele Linux-Healthprobes), `tests/security/test_secrets.py` (mit PTV21_DOCKER=1 echte Mount-/Egress-/Canarygrenzen) und `tests/security/test_host_transport.py` (tatsächlicher Windows-Loopback, Sitzung/Host/Origin/Forwarded, binäre STDIO-Integrität). Health-Vertrags-/Negativfälle: `tests/contracts/test_runtime_health.py`; Redaction-/OTel-/Samplerfälle: `tests/unit/test_runtime_telemetry.py` und `test_runtime_metrics.py`. T-RSK-001/008 bleiben durch DISABLED/PAUSE_NEW, fehlende Marktbeweise und explizites Paperflag geschlossen. Ausgeführte Ergebnisse und verbleibende menschliche Providerprüfung stehen getrennt in `docs/evidence/ptv-21-runtime-boundaries.md`; geplante Matrixzeilen sind weiterhin keine pauschale PASS-Behauptung.

## Status und Gates

Dies ist eine **Testzuordnung**, kein pauschaler Ausführungsbericht. PTV-23 hat die historischen Planpfade am2026-10-18 auf die tatsächlich implementierten, teils zusammengefassten Testdateien abgeglichen. Konkrete Ausführungsergebnisse und Grenzen stehen in docs/evidence/ptv-23-final-acceptance.md und den dort verlinkten Tasknachweisen. Der verbleibende generierte testStatus planned-not-executed beschreibt ausschließlich den statischen Validator und wird nicht zu einem erfundenen Runtime-PASS umgeschrieben. Die Definition of Ready verlangt valide Specs, Verträge, Beispiele und vollständige Zuordnung. Die spätere Runtime-Zertifizierung verlangt zusätzlich grüne Tests, Recovery-Evidenz und eine unabhängige Sicherheitsprüfung. Kein Test darf einen Live-Endpunkt erreichen. Normale CI nutzt ausschließlich MockDecisionEngine, Replay und FakeBroker. Ein echter Paper-Smoke ist optional, menschlich ausgelöst und aus normaler CI ausgeschlossen.

Die Pyramide hat fünf Ebenen: Unit/Property für Zahlen, Zeit und Zustandsregeln; Contract/Schema für API, Events und Adapter; Integration für Transaktionen, Jobs und Dashboard; Recovery/Failure Injection für Prozess- und Dienstausfälle; Security für Prozessrechte, Secrets und lokale Kontrollgrenzen. Jeder Test speichert Requirement-ID, Fixture-/Konfigversion und Ergebnis in der CI-Evidenz. Die Zuordnung unten ist vollständig für die 40 Spec-Szenarien.

## Requirement-Test-Matrix

| Test-ID | Ebene und implementierter Testpfad | Messbarer Kernnachweis |
|---|---|---|
| T-MKT-001 | Contract: `tests/unit/test_market_state.py` | Fremdes Instrument/Intervall: 0 angenommene Bars |
| T-MKT-002 | Unit: `tests/unit/test_market_state.py` | Offene/korrigierte Bar: 0 still mutierte Inputs |
| T-MKT-003 | Property: `tests/property/test_decimal.py` | NaN/Infinity/Überlauf/Skalenverlust abgelehnt; UTC round-trip |
| T-MKT-004 | Property: `tests/unit/test_market_state.py` | Für jeden Input gilt `availableAt <= asOf` und Barende `<= asOf` |
| T-MKT-005 | Unit: `tests/unit/test_market_state.py` | Lücke/Duplikat/Widerspruch ergibt keine freigabefähige Entscheidung |
| T-MKT-006 | Unit: `tests/contracts/test_alpaca_market.py` | Feiertag, Halbtag und beide DST-Wechsel: Aktien zu, Krypto nach bestätigter Capability offen |
| T-MKT-007 | Contract: `tests/unit/test_market_state.py` | Neutrales Bündel enthält Herkunft/Konflikt, keine Orderfelder |
| T-MKT-008 | Contract: `tests/contracts/test_alpaca_market.py` | Unbestätigte Intervalle/Assets/News sind nicht verfügbar |
| T-DEC-001 | Integration: `tests/unit/test_decision.py` | Korrektur erzeugt neuen Hash; alter Input bytegleich |
| T-DEC-002 | Contract: `tests/contracts/test_jev.py` | SHORT/unerlaubte Choice wird zu keiner Proposal-Freigabe |
| T-DEC-003 | Contract: `tests/contracts/test_jev_wire.py` | Choice/Score/Noul/Confidence getrennt; Sizingfeld ohne Wirkung |
| T-DEC-004 | Unit: `tests/contracts/test_jev.py` | Timeout, Schemafehler, fehlendes Feld, stale, niedrige Confidence: 100 % ABSTAIN |
| T-DEC-005 | Contract: `tests/contracts/test_jev_wire.py` | Mehrdeutige Antwort abgelehnt; getrennte Frageversionen |
| T-DEC-006 | Integration: `tests/unit/test_decision.py` | Modell/Prompt/Frage/Inputhash/Verteilung/Trace/TTL erhalten |
| T-DEC-007 | Recovery: `tests/integration/test_replay.py` | Mock/Baseline bei identischen Inputs bytegleich; 0 Broker-Submits |
| T-DEC-008 | Contract: `tests/contracts/test_jev.py` | Nicht unterstützter Input/Ausfall führt zu ABSTAIN |
| T-RSK-001 | Unit: `tests/security/test_execution_isolation.py` | DISABLED-Default; LIVE-Config abgelehnt; REPLAY/SHADOW: 0 Submits |
| T-RSK-002 | Recovery: `tests/integration/test_pause_resume.py` | Restart bleibt PAUSE_NEW bis Health und Reconciliation grün |
| T-RSK-003 | Contract: `tests/contracts/test_alpaca_paper.py` | Short/Hebel/unbestätigte Orderart/Fractional/Session abgelehnt |
| T-RSK-004 | Unit: `tests/property/test_risk.py` | Gleicher Snapshot/Policy ergibt gleiche Decision; Pflichtlücke fail-closed |
| T-RSK-005 | Recovery: `tests/recovery/test_submit_crash.py` | TTL-, Policy-, Pause-, Session- oder Datenwechsel vor Submit: 0 Submits |
| T-RSK-006 | Property: `tests/property/test_risk.py` | Grenzwerte 1 Position/Asset, 2 gesamt, 10 % Notional, 1 % Tagesverlust; exakte Decimalgrenzen |
| T-RSK-007 | Integration: `tests/integration/test_risk_races.py` | Zwei konkurrierende, zusammen zu große Vorschläge: höchstens 1 Erfolg; Budget nie negativ |
| T-RSK-008 | Security: `tests/security/test_execution_isolation.py` | Agent/API/Watchdog ohne Submit-Port/Key; Paper-Smoke ohne Opt-in: 0 Requests |
| T-RSK-009 | Recovery: `tests/recovery/test_submit_crash.py` | Timeout/Crash: 1 stabile clientOrderId, Reservation gehalten, 0 Blind-Retries |
| T-RSK-010 | Property: `tests/recovery/test_reconciliation.py` | Alle Übergänge; Teilfüllung/Cancel-Fill in beiden Reihenfolgen, Fillmenge monoton, nie doppelt |
| T-RUN-001 | Integration: `tests/integration/test_ledger.py` | Nach DB-Restart vollständige Proposal→Risk→Intent→Report-Kette |
| T-RUN-002 | Recovery: `tests/recovery/test_reconciliation.py` | Doppelte/ungeordnete Events: genau 1 wirtschaftliches Delta |
| T-RUN-003 | Integration: `tests/integration/test_jobs_outbox.py` | Leaseverlust/Fencing: alter Worker 0 Commits; Retrylimit/Dead Letter sichtbar |
| T-RUN-004 | Recovery: `tests/integration/test_jobs_outbox.py` | Crash vor Publish: Event nach Restart vorhanden, Consumerwirkung einmalig |
| T-RUN-005 | Security: `tests/integration/test_acceptance.py` | Jede Mutation hat Trace/Akteur/Reason/UTC; keine Canaries in Audit/Logs |
| T-RUN-006 | Security: `tests/integration/test_acceptance.py` | Paper-Key nur Execution; Promptinjection löst 0 Tools/Installationen aus |
| T-RUN-007 | Integration: `tests/integration/test_failures.py` | Datenstillstand, DB/Broker/Jev-Ausfall separat sichtbar; neue Einstiege gesperrt |
| T-CTL-001 | Contract: `tests/contracts/test_control_api.py` | OpenAPI-Beispiele, Decimalstrings, UTC, interne DTOs; keine Vendorpayloads |
| T-CTL-002 | Contract: `tests/integration/test_control_http.py` | Idempotency-Key/Payloadhash, 412/428, traceparent; Wiederholung ohne Zweitwirkung |
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
- **Optionaler Paper-Smoke:** Der tatsächliche manuelle Ablauf über `services.execution.paper_smoke` ist nie Teil normaler CI. Er verlangt menschliche Freigabe, Vordergrund-TTY, explizites Paper-Flag, bestätigten festen Paperhost/Kontofingerprint, höchstens einen ganzen Anteil als LIMIT-BUY innerhalb1000USD einschließlich Cashpuffer und Status/Storno oder Fill. `tests/paper/test_smoke.py` und `tests/unit/test_paper_smoke_plan.py` prüfen die Guards und begrenzte Orchestrierung vollständig offline und dürfen in normaler CI laufen. `tests/integration/test_paper_smoke.py` prüft echte eingeschränkte PostgreSQL-Rollen mit synthetischen Providertransporten: geschützte Execution-Freigabe, Worker-Digest/TTL/Abstention/Replay, Rohbar-Bootstrap bei Pause, explizite Recovery und den gezielten Risk-/Submit-/Cancel-/UNKNOWN-Pfad. Fehlende Guards brechen vor Provider-/Secretzugriff ab; kein Test startet echte Providerrequests. Runbook: `docs/runbooks/paper-smoke.md`. Echte menschliche Paper-Evidenz bleibt separat als nicht ausgeführt markiert.

## Abnahme-Evidenz

Ein späterer Apply liefert Testreport mit Fixture-/Policy-/Contractversionen, Exitcode, Timestamp UTC, anonymisierter Trace-ID und Link auf redigierte Logs. Blockierend sind jede Doppelorder, jede stille SSE-Lücke, jede Budgetüberschreitung, jedes Secret-Canary-Leck, ein erreichbarer LIVE-Pfad, fehlende Riskreferenz, verlorene Fills, fehlende Reconciliation vor Retry und jeder Vertrag- oder Clientdrift. Erst nach vollständiger Runtime-Evidenz darf die Implementierung als sicher zertifiziert werden.

PTV-18 ergänzt tatsächliche HTTP-/Sitzungstests sowie `tests/integration/test_control_http.py` (durable Replaybelege, ETags, eingeschränkte Rolle, Parallelität, transaktionaler Rollback und Initialisierung ohne Policy). `tests/contracts/test_control_events.py` prüft typisierte Control-Ereignisse und fehlende Policy-Metadaten ausschließlich für Notfall-Latches. Ausführungsevidenz wird separat in docs/evidence/ptv-18-control-api.md aufgezeichnet; die generierte Matrix bleibt die Testzuordnung, kein pauschaler Runtime-Pass.

PTV-19 prüft den atomaren Snapshot bei parallelem Pause-/Publish-Commit, erneute Services mit demselben persistierten Cursor, unveränderte Seitenmitgliedschaft bei parallelen Inserts und Heartbeats, Cursorablauf sowie konkurrierende Outbox-Publisher gegen PostgreSQL. Endliche ASGI-/Generatorprüfungen in tests/contracts/test_supervisor_transport.py decken Last-Event-ID, Heartbeat, Reset/Close und Sessionwiderruf ab; tests/contracts/test_supervisor_projections.py und test_supervisor_events.py prüfen normalisierte Payloads. Laufzeitevidenz separat in docs/evidence/ptv-19-snapshot-sse.md.


PTV-25 ergänzt Offline-Activitiesverträge sowie tatsächliche PostgreSQL-Kontoreconciliation, Teilfills/Cancelrennen, Wiederanlauf/Deduplizierung, unvollständige Belege, Gebührenunsicherheit und private Rollen-/Auditgrenzen. Der synthetische Aktien-Testkalender verwendet eine nur im Test verschobene Zeitzone, damit echte UTC-Datenbankzeit und unveränderte 09:30–16:00-Gates zeitunabhängig prüfbar bleiben; dies zertifiziert keine tatsächliche Börsensitzung. Prozessabbrüche sind injizierte BaseException-Fälle, keine OS-Kill- oder Deploymentprüfung. Bestätigte FEE/CFEE werden konto- beziehungsweise assetbezogen geprüft; unbekannte Ordergebühren bleiben unbekannt. Laufzeitergebnisse: docs/evidence/ptv-25-paper-reconciliation.md.
