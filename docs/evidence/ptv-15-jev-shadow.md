# PTV-15: Jev-Adapter und lokale Shadowevidenz

Prüfstand 2026-10-14. Requirements REQ-DEC-002..006/008 und REQ-RSK-001; Tests T-DEC-002..006/008, T-RSK-001. Die aktuelle offizielle TypeSafe-API ist im [Wireprofil](../development/jev-wire-contract.md) dokumentiert. Modell `jev-1.13.0`, HTTPX 0.28.1, feste HTTPS-URL; keine neue SDK-Abhängigkeit. Anwendungskonfiguration ist disabled/SHADOW. Es wurden ausschließlich synthetische Credentials und MockTransport verwendet.

## Ergebnisse

| Prüfung | Beobachtete Evidenz |
|---|---|
| Kombinierte lokale Suite und Fake-Recovery | 199 passed in 75.07s; Contracts, Domain, Risk, Replay, Shadow und drei PostgreSQL-Crash-/Timeoutfälle |
| Dauerhafter Verbrauch | 1 passed in 23.74s; tatsächlicher Worker, Migration 0005, Neustart und Cache-Replay bewahren 100/10 Token; historische Werte null/null, ungültiges Paar SQLSTATE 23514; kein Proposal/Order |
| Ledger-Migration und Restore | 2 passed in 52.97s; unveränderte Erstmigration ohne Verbrauchsfelder verifiziert, anschließend vollständige Migration; Daten-/Rollenfingerprints beim Backup/Restore identisch |
| 100-Fall-Shadowevaluation | drei Läufe, je 70 lokale Mockrequests, 30 Vorabenthaltungen, stabile Semantikhashes, 100 tatsächliche ABSTAIN, 70 gültige Shadow-LONG; akzeptierte Schemafehler 0 |
| Fehlerklassen | sieben Klassen, jeweils 100/100 ABSTAIN und keine freigegebene Shadowantwort; 30 Inputs werden jeweils vor I/O abgewiesen |
| Latenz/Verbrauch | beobachteter lokaler p95 1ms, Grenzwert <=5000ms; 7000 Input-/700 Outputtoken aus synthetischen Antworten; keine errechneten Kosten |
| Eignungslabels | synthetische Input-Eignung, Coverage 70/100, 30 ausgeschlossen; Brier 0.04, mittlere Confidence 0.8, Eignungsrate 1, Kalibrierungslücke 0.2 |
| HTTP-Vertrag/Client | oasdiff gegen main `90637d5` ohne Breaking Change; zweimal 65 Dateien bytegleich generiert, TypeScript-/ABSTAIN-Prüfung bestanden; ausschließlich mechanische Clientänderung |

Corpus SHA256 `09aaaed9ab608bf4e340adfd5af6d7173bb3725e090cf7f2fb9a58d4fe4229fe`; Konfiguration `15debc6a42f7b55eb9d980309ef78e4149ee35364671f81ce89e5cdf9f677af6`; Labels `synthetic-input-suitability.v1`, SHA256 `42d81c01cc31e7e9e8705809604a8ffcd358c9d46efe54781224f59544724a3c`. Reproduzierbarer Bericht über `evaluation_summary()`; lokaler Snapshot unter ignoriertem `.tooling/output/ptv-15-report.json`.

Die 70 gültigen Fälle teilen dieselben Beobachtungen und erhalten eine feste Mockantwort. 70/70 projizierte Baselineübereinstimmung belegt die Vergleichspipeline, keine empirische Modellgüte. Synthetische Brierwerte sind ausdrücklich kein Zertifikat realer Kalibrierung. Reale Anbieterperformance, Qualität und Abrechnung bleiben ungeprüft; diese Evidenz erlaubt keine aktive Handelsentscheidung.

## Safetyreview

| Invariante | Evidenz/Ergebnis |
|---|---|
| Kein Brokerzugriff, deterministische Gates | Adapter/Replay besitzen keinen Brokerport; Planner erhält ausschließlich ABSTAIN; drei bestehende Fake-Recoveryregressionen grün |
| LIVE unerreichbar/default fail-closed | Config erlaubt nur SHADOW und startet deaktiviert; unsafe mode/model/thresholds werden abgewiesen |
| Decimal/UTC/Frische | typisierte Wahrscheinlichkeiten und UTC; Vorprüfung und erneute Zulassung nach Antwortkonstruktion; tatsächliche Gesamtfrist/TTL geprüft |
| Pause/Reconciliation unabhängig | keine Jev-Abhängigkeit in diesen Diensten; vorhandene Isolations- und Riskprüfungen grün |
| Secret-/Transportgrenze | feste TLS-URL, trust_env false, keine Redirects; nur exakter MockTransport injizierbar, keine Credentials gelesen; Fehlertexte werden verworfen |
| Audit und Verbrauch | unveränderliche normalisierte DecisionResult, gepaarte begrenzte BigInteger-Verbrauchsfelder; Restart-/DB-Constraintprüfung bestanden |

Unabhängiger Quellreview schloss die früheren Befunde zu Schwellenwerten, Frische, Antwort-IDs, dauerhaftem Verbrauch und Shadowzulassung. Die verbleibende Anbieterzertifizierung ist oben ausdrücklich getrennt. [Runbook](../runbooks/jev-evaluation.md).
