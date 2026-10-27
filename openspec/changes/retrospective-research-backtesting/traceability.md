# Requirement-/Scenario-Traceability und Teststrategie

Alle folgenden Laufzeittests sind geplant. Propose führt Struktur-/Planprüfungen aus; die vorhandene Implementierungsabnahme wird nicht für neue Funktionalität ausgegeben.

| Requirement | Szenarien | Primäre Pakete | Prüfebene |
|---|---|---|---|
| REQ-RRB-001 | T-RRB-001/002 | 01/02/04/06/07 | Default, explizites Strict, Fehler-/Hashparität, kein Fallback |
| REQ-RRB-002 | T-RRB-003/004 | 02/03/06 | echte PG-Parallelcommits, Retention, Snapshotseiten, Budget |
| REQ-RRB-003 | T-RRB-005/006 | 02/04 | unabhängige Input-/Outcomeperturbation, Fillgrenze |
| REQ-RRB-004 | T-RRB-007/008 | 01/02/03/04/06 | deterministische unabhängige Decimalrechnung, Legacyhashes |
| REQ-RRB-005 | T-RRB-009 | 03/06 | Crash nach Bindung, Restart, alter Fence, atomare Publikation |
| REQ-RRB-006 | T-RRB-010/011 | 01/06/07 | echte HTTP-/Browserkette, Modi auf jeder Ergebnisseite, Auth |
| REQ-RRB-007 | T-RRB-012 | 03/04/06/08 | SQLrollen, Container-/Mountcanaries, Ledgerhashes, Last |
| REQ-RRB-008 | T-RRB-013/014 | 01/05/06/07 | Berichtsfelder, null-Nenner, unvollständige Prüfungen |
| REQ-RRB-009 | T-RRB-015 | 05/08 | OHLCV, Bar-/Kalenderlücken, Feed-/Adjustmentmischung |
| REQ-RRB-010 | T-RRB-016/017/018 | 05/07/08 | bekannte/unbekannte Maßnahmen, Dividenden, Sprunggrenzen |
| REQ-RRB-011 | T-RRB-019 | 05/07/08 | UNKNOWN/DENIED, IEX/Survivorship/Preisrendite, kein Export |
| REQ-RRB-012 | T-RRB-020 | 01/03/05/06/07 | Vorschauhash/Snapshotbindung, Cursor, aktuelle Warnungen |

RRB-08 übernimmt die gemeinsame Integrationsabnahme aller Zeilen, nicht die verspätete Erstprüfung der vorherigen Pakete. Jeder Task enthält eigene Tests und Akzeptanz. Fachliche Baselines werden vor Simulatoränderung aus dem verifizierten Strict-Stand fixiert; alte Goldenwerte nicht wegen neuer Ergebnisse aktualisieren. Strenge Parität umfasst Materialisierung, Simulator, Fehlercodes, Input-/Manifest-/Resultathashes und alte Idempotenzschlüssel.

## HTTPzuordnung

createBacktestRun/listBacktestRuns/getBacktestRun/getBacktestResults: REQ-RRB-001/004/005/006. Neue Operationen createBacktestAssessment/getBacktestAssessment/getBacktestReliabilityReport: REQ-RRB-002/008/009/010/011/012. operationIds bei RRB-01 verbindlich im kanonischen Vertrag festlegen und Routen-/DTO-/Beispiel-/Clientparität bei RRB-06 vollständig nachweisen. Strategiecatalog bleibt modusneutral, listet verfügbare Modi. Keine neue SSEoperation.

## Safetyabnahme

Echte PostgreSQLmigrationen mit tatsächlichen Runtimeidentitäten, öffentliche authentifizierte HTTPoperationen und Browser über denselben Stack. Provider ausschließlich lokales HTTPdouble; der Backtestworker benötigt es niemals. Aufrufzähler für Broker/Provider/Jev bleiben null. Nichtleere deterministisch sortierte Finanztabellen vor/nachher fingerprinten, nicht nur Zeilenzahlen oder leere Tabellen. Relevante Bestandstabellen und Kontrollen aus RDR-/BKT-Abnahme wiederverwenden. Pause/Kill/Reconciliation unter gleichzeitiger Vorschau-/Backtestlast mit den bestehenden Grenzwerten messen. Bestandsregressionen inklusive Fees, Reservierungen, unbekannten Orderzuständen und Reconciliation bleiben unverändert. Source-/Secretscan nur versionierbare Quellen, niemals private Schlüsseldateien.

## Contract- und Migrationsabnahme

Additive kanonische OpenAPI-/Schema-/Beispieländerungen, Breakingvergleich gegen verifizierte Basis, zweimalige bytegleiche Clientgenerierung, TypeScriptbuild, echte Routentests. Altzeilen/Hashes vor/nach Migration vergleichen; alter Worker lehnt neue Varianten ab. Rollback erhält alle gebundenen Daten und Reports, deaktiviert neue Erstellung und übernimmt keine unbekannten Versionen.

## Definition of Ready

Erfüllt erst mit striktem OpenSpec, vollständiger Traceability, konfliktfreiem unabhängigen Planreview, acht eindeutigen offenen Issues samt nativen DAG und verifiziertem veröffentlichten Planstand. Danach ist ein eigener Apply-Auftrag nötig. Reale Providerrechte, historische Quellenqualität und Profitabilität bleiben externe Grenzen; synthetische Tests ersetzen sie nicht.
