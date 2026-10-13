# Trading Safety Review — Spezifikationsstand

## Ergänzung: Apply PTV-01

Buildgrenze geprüft: `services/bootstrap.py` erzwingt DISABLED/fake und lehnt externe Anbieter-Konfiguration ohne Wertausgabe ab (vier Unit-Tests bestanden). Das Image enthält keine Credentialdateien, läuft als 10001/read-only ohne Capabilities; PostgreSQL als postgres/read-only ohne Capabilities. Lokales internes Netz, nur Loopback-DB-Port, DB-Secret ausschließlich am PostgreSQL-Service. Unabhängiges Review fand eine unsichere ACLreihenfolge; Directory-DACL vor Erzeugung und File-DACL für bestehende/neue Datei jetzt explizit geschützt und boolesch geprüft. Clean-Volume-Smoke auf Linux Python 3.13.16/PG 18.6 bestanden; temporäres Testprojekt entfernt. Details/Kommandos in implementation-evidence.md.

Dies zertifiziert die Build-/Secretgrenze dieses Tasks. Es existieren noch keine Risk-/Execution-/Reconciliationprozesse; die unten geplanten Tradinginvarianten bleiben bis zu ihren tatsächlichen Laufzeitprüfungen unverified. Der folgende Abschnitt beschreibt den ursprünglichen Planungsreview.

## Scope und Ergebnis

Geprüft wurden `AGENTS.md`, `proposal.md`, `design.md` D-01 bis D-10, alle fünf Delta-Specs, `test-strategy.md` und die geplanten Runbooks. Prüfung nach `.agents/skills/trading-safety-review/SKILL.md`, Stand 2026-10-12. **Ergebnis für die dokumentierte Planung: bestanden, mit unten genannten Nachweisen und Restprüfungen.** Es gibt noch keine Anwendung, keine ausgeführten Runtime-Tests und keine Brokerverbindung. Runtime-Verhalten ist deshalb **unverified** und darf nicht als implementiert oder zertifiziert bezeichnet werden. Dieser fehlende Runtime-Nachweis blockiert die spätere Implementierungszertifizierung, aber für sich allein nicht die Definition of Ready der reinen Planung.

## Sicherheitscheckliste

| Invariante | Befund und konkrete Spezifikationsevidenz | Künftiger Test und Status |
|---|---|---|
| Agenten ohne Broker-Submit | **Spec pass.** D-01 trennt Worker/Agent von Execution, DB-Rollen und Egress; REQ-RSK-008 begrenzt Submit auf Execution. | T-RSK-008 `tests/security/test_execution_isolation.py`; Runtime unverified |
| Deterministische RiskDecision und Execution-Gates | **Spec pass.** D-04 serialisiert Accountbudget und erneute Submitprüfung; REQ-RSK-004/005. Keine Jev-Antwort darf das Gate ersetzen. | T-RSK-004/005; Runtime unverified |
| LIVE unerreichbar | **Spec pass.** REQ-RSK-001 und D-01/D-04 verbieten LIVE-Config und reale Orderhosts; Replay/Shadow haben keinen Submit. | T-RSK-001/008 plus Host-Allowlist-Test; Runtime unverified |
| DISABLED/fail-closed und Restart | **Spec pass.** REQ-RSK-001/002, D-04 und D-06 verlangen DISABLED, nach unsicherem Start mindestens PAUSE_NEW. | T-RSK-001/002; Runtime unverified |
| Decimal und UTC | **Spec pass.** REQ-MKT-003, REQ-RUN-007, REQ-CTL-001 und D-03 definieren Dezimalwerte, UTC und Exchange-Kalender/DST. | T-MKT-003/006, T-RUN-007; Runtime unverified |
| Kill-Switch/PAUSE_NEW ohne Jev/UI | **Spec pass.** REQ-CTL-005 und D-04 legen Watchdog-/CLI-Pfad, persistenten Latch und in-flight-Grenze fest. | T-CTL-005; Runtime unverified |
| Credentials nur Execution und Redaction | **Spec pass.** REQ-RUN-006, D-01/D-09, restriktive Mounts und Egress. Papier- und spätere Live-Keys sind getrennt. | T-RSK-008, T-RUN-005/006, `tests/security/test_redaction_canary.py`; Runtime unverified |
| Unveränderlicher Vorschlag/Riskreferenz/Audit | **Spec pass.** REQ-DEC-001, REQ-RSK-004, REQ-RUN-001/005 und D-03/D-06 verbinden Proposal→Risk→Intent→Report mit Trace-ID und append-only Audit. | T-DEC-001, T-RSK-004, T-RUN-001/005, T-CTL-006; Runtime unverified |
| Atomare Reservierung | **Spec pass.** REQ-RSK-007 und D-04 zählen Positionen, offene Orders und Reservierungen unter Account-Gate. | T-RSK-007; Runtime unverified |
| Ablaufprüfung direkt vor Submit | **Spec pass.** REQ-RSK-005 und D-04 prüfen TTL, Policy, Portfolio, Capability, Session, Daten und Pause erneut. | T-RSK-005; Runtime unverified |
| UNKNOWN hält Reservierung | **Spec pass.** REQ-RSK-009, D-05 und `docs/runbooks/recovery.md` verbieten Freigabe bei unklarem Status. | T-RSK-009; Runtime unverified |
| Timeout-Reconciliation vor Retry | **Spec pass.** REQ-RSK-009, D-05 und Crashmatrix verlangen Brokerabfrage per stabiler ID vor erneutem Submit. Kein Exactly-once-Versprechen. | T-RSK-009, `tests/recovery/test_submit_crash_matrix.py`; Runtime unverified |
| Teilfüllung/Cancel-Fill-Rennen | **Spec pass.** REQ-RSK-010 und D-05 definieren monotone kumulative Fills, Deduplikation und verbleibende Reservierung. | T-RSK-010, T-RUN-002; Runtime unverified |
| Jev-Ausfall sperrt Pause/Reconciliation nicht | **Spec pass.** REQ-DEC-004/008, REQ-CTL-005, D-04/D-09 trennen ABSTAIN von Kontroll- und Recoverypfad. | T-DEC-004/008, T-CTL-005; Runtime unverified |

## Prüfung und verbleibende Grenzen

Dokumentprüfung: Alle 40 Requirement-IDs besitzen in `test-strategy.md` genau eine primäre Testzuordnung; die Teststrategie nennt zusätzlich Crash-, Contract-, Security- und Paper-Smoke-Querschnittstests. Die Runbooks kennzeichnen sämtliche CLI-/Compose-Beispiele als künftig und senden keine Orders. Es wurden keine Credentials, Brokerendpunkte oder Live-Tests benutzt. `rg`-Suche nach Sicherheitsbegriffen in Design und Specs sowie Lesen aller betroffenen Dateien ergaben keinen fachlichen Widerspruch zwischen Modus-, Risk-, Recovery- und Secretgrenzen.

**Offene Implementierungsrisiken, keine festgestellten Spezifikationsabweichungen:** Der spätere Apply muss das Account-/Submitgate samt Pause-Race tatsächlich serialisieren, Fencing gegen alte Worker durchsetzen, die definitive Broker-Nichtannahme nach Timeout anhand der dokumentierten Paper-API beweisen und Snapshot/Cursor ohne Commitlücke implementieren. Für den lokalen Kontrollsocket und die browserseitige Session ist vor Runtime-Freigabe ein konkreter Contract mit Negativtests nötig (D-08). Falls einer dieser Nachweise fehlschlägt, ist die Implementierungszertifizierung blockiert; die reine Planungs-DoR ist anhand anderer geforderter Artefakte separat zu beurteilen.

**Ausführungsresultate:** Keine Runtime-Tests ausführbar, weil noch keine Anwendung existiert. Alle genannten Testpfade sind geplant und stehen auf `unverified`. Ein opt-in Paper-Smoke ist erst nach menschlicher Freigabe und bestandener Fake-/Runtime-Zertifizierung erlaubt.
