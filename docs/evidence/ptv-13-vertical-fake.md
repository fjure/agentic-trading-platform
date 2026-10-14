# PTV-13: Vollständiger lokaler Fake-Trade

## Nachweisgrenze

Die neuen PostgreSQL-Integrationstests verbinden die tatsächlichen Module für geschlossene Marktdaten, MockDecisionEngine, Planner, atomare Risikoreservierung, Execution-Gates und Fake-Fill-Reconciliation. Die isolierte bestehende Fixture stellt ausschließlich synthetische Basisdaten für Account, Policy, Portfolio und Rollen bereit. Die untersuchte Tracekette muss neue, durch die Worker-Module erzeugte Inputs, Entscheidungen und Vorschläge verwenden. Der Start- und Wiederanlaufpfad darf eine Pause nicht durch direkte Kontextschreiboperationen umgehen.

Die Testsession ist ausdrücklich synthetisch und versioniert. Sie erlaubt frische, abgeschlossene Eingabekerzen unabhängig von der echten XNYS-Uhrzeit; Produktionskalender und Fähigkeiten werden damit nicht zertifiziert. Prozessabbruch wird deterministisch als BaseException an der Execution-/Fake-I/O-Grenze injiziert. Dieselbe Fake-Brokerwelt überlebt den neuen Execution-Owner. Dies ist kein OS-Kill-, PostgreSQL-Restore-, Deployment- oder externer Paperbroker-Nachweis.

## Zuordnung

| Requirement / Test-ID | Nachweis |
| --- | --- |
| REQ-MKT-002 / T-MKT-002 | Geschlossene Eingabekerzen durch die tatsächliche Marktnormalisierung; offene Kerze nicht freigabefähig |
| REQ-DEC-001 / T-DEC-001 | Korrektur als neuer persistierter Input und anderer Inputhash; gespeicherter Ursprung vor/nach Korrektur bytegleich |
| REQ-RSK-004 / T-RSK-004 | Tatsächlicher Planner und RiskDecision vor Fake-Submit, vollständige langlebige Referenzkette |
| REQ-RSK-007 / T-RSK-007 | Zwei vollständig geplante SPY-/QQQ-Vorschläge konkurrieren um Cash; genau eine Freigabe, idempotente Reservierung und kein Overspend |
| REQ-RSK-009 / T-RSK-009 | Crash/Timeout, sichtbares UNKNOWN und gehaltene Reservation, kein blinder Zweit-Submit |
| REQ-RSK-010 / T-RSK-010 | Teil-/Endfill idempotent; ergänzender bestehender Cancel-/Fill-Race-Test |
| REQ-RUN-004 / T-RUN-004 | Dauerhafte Orderereignisse und nachvollziehbare Outbox-Zustellung nach Unterbrechung |
| REQ-RUN-005 / T-RUN-005 | Konkrete Auditverknüpfungen je Pipelinephase mit Trace, Actor, Reason und Policy |
| REQ-CTL-005 / T-CTL-005 | Unabhängige Pause bei Decision-Ausfall; separat explizite Recovery nach geklärter Orderlage |

Testdateien: `tests/integration/test_vertical_fake.py`, `tests/recovery/test_vertical_failures.py`; privater Gasthelper: `tests/integration/vertical_support.py`. Sechs neue PostgreSQL-Szenarien bestanden in 150.90s: happy, budget-race, decision-down, crash-before, crash-after und timeout. Nach der abschließenden Report-Aggregatkorrektur bestand der Happy-Pfad mit expliziter Aggregatprüfung erneut: 1 passed in 25.95s. Alle elf bestehenden Submit-/Crashregressionen bestanden in 279.27s; 109 Contract-/Domain-/Broker-/Riskregressionen in 3.68s. Ruff, strikte OpenSpec-Validierung, 40REQ/23Tasks-DAG, neun Schemas mit Beispielen, oasdiff gegen main e3497b9 und Source-Scan 264/0 bestanden. HTTP und generierter Angular-Client bleiben unverändert; die Regenerationsprüfung wird zusätzlich durch die bestehende Contract-CI ausgeführt. Unabhängiger abschließender Quellreview ohne offene Befunde. Zwei zuvor gestoppte Subagenten lieferten wegen Nutzungslimit keinen Abschlussnachweis; Hauptagent übernahm ihre Integration und Laufzeitprüfungen, neuer unabhängiger Reviewer prüfte den endgültigen Stand.

Der Durchstich fand einen fehlenden Pre-I/O-Eventnachweis. `services/execution/intents.py` schreibt nun SUBMITTING-Order, dazugehörigen unveränderlichen ExecutionReport, Audit und typisierte Outbox gemeinsam in der ersten Transaktion. Report und Ereignis verwenden stabile Intent-bezogene IDs; das Aggregat ist konsistent mit den Folgeereignissen die Report-ID mit Version1. Es werden keine Submit-/Risk-/Ownergates gelockert. Ein Zustellabbruch trifft gezielt ein Execution-Ereignis des vertikalen Trades; nach Publisher-Neustart wird dieselbe ID wieder zugestellt und die dauerhafte Consumer-Receipt verhindert eine zweite Verarbeitung.

Die Auditprüfung verlangt konkrete Entity-/Aktionspaare für Decision, Proposal, RiskDecision, Reservation, Intent und SUBMITTING-Order sowie UTC, Actor, Reason und an den Risk-/Executionphasen Policyversion1. Der finale doppelt reconciliierte Fill hält die Position bei1, Einstand100, Cash0.01, Exposure100 und Equity2000; Reservation0, genau zwei Broker-Fillreports. Keine zusätzliche Order oder doppelte Positionswirkung. UNKNOWN vor bestätigtem Brokerkontakt bleibt konservativ offen und hält100.01 Budget; ein leeres Inventar hebt diese Unsicherheit nicht automatisch auf. Nach bestätigter Akzeptanz werden Fill und aktuelle Recoveryevidenz vor erneuter RUNNING-Freigabe geprüft.

Ergänzende Regression: `pytest tests/recovery/test_reconciliation.py -q -k cancel-race` bestand mit **1 passed, 17 deselected in 23.19s**. Dieser bestehende wirtschaftliche Race-Test wird ausdrücklich getrennt vom neuen vollständigen Durchstich gezählt.
