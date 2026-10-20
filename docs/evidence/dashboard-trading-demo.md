# Dashboard-Demo: integrierte Abnahme

Stand: 2026-10-20. Nutzerfreigegebener Change `dashboard-trading-demo`, sechs Tasks DEMO-01 bis DEMO-06. Die sichtbare Simulation, private Alpaca-/Jev-Einrichtung, frei wählbares USD-Startbudget und vorhandene Mock-/Baselinevarianten sind implementiert. OpenSpec bleibt aktiv; Spec-Sync und Archivierung erfolgen nur auf gesonderten Auftrag.

## Tatsächliche Prüfevidenz

- Lokale Regression: **814 passed, 19 ausgewiesene Skips, 82,66 s**. Plattformabhängige Zusatzprüfungen separat: **54 Linux-Setupprüfungen** und **3 tatsächliche Deploymentprüfungen** bestanden. Damit werden nicht pauschal alle historischen Skips als ausgeführt behauptet.
- Browserregression: **20 passed, 24,3 s**; der absichtlich getrennte Realtest wird in dieser Fixture-Suite übersprungen. Separater vollständiger Realtest: **1 passed, 21,6 s**, Chromium → kanonische HTTP-API → PostgreSQL, inklusive Cleanup. Zwei gefüllte synthetische Orders mit 100/200 USD Startbasis, 95/195 USD Cash, je Position 1 und persistenter Tracekette; Reload unverändert. 0,01 USD wird ohne Order fail-closed abgewiesen. Normales Testledger unverändert.
- Tatsächliche PostgreSQL-Demokette einschließlich Idempotenz, Recovery und Pause: bestanden, 66,59 s. Fünf Provisionierungs-Driftfälle bestanden; zusätzliche Rollenmitgliedschaft oder Rechteabweichungen werden nicht automatisch repariert.
- Budget: 26 Unitprüfungen und sechs PostgreSQL-Szenarien bestanden. Abschließender echter HTTP-/SQL-Nachweis für Profilspeicherung, Versionierung, Rechte und öffentlich projiziertes Audit bestanden, 23,90 s. Neue Auditaktion ausdrücklich zugelassen; keine allgemeine Freigabe unbekannter Payloads.
- Kanonischer Vertrag: 31 Operationen, 14 Schemas; Beispiele validiert, additive Kompatibilität geprüft. Zweimal reproduzierbare Generierung von 88 Clientdateien, TypeScriptprüfung und Angularproduktionsbuild bestanden. Generierten Code nicht manuell verändert.
- Unabhängiger begrenzter Safetyreview ohne verbleibende bestätigte P1/P2; zusätzlich 17 Review-Unitprüfungen bestanden. Kontrollintention wird vor potenziell blockierender Provisionierung persistiert und vor Fakeannahme/-Fill erneut unter Metadatenzeilensperre geprüft.

Einzelbelege: [Verträge](demo-01-contracts.md), [Runtime](demo-02-runtime.md), [Dashboard](demo-03-dashboard.md), [echter Browser](demo-04-browser.md), [Einrichtung](demo-05-setup.md), [Budget](demo-06-budget.md).

## Grenzen und Bedienung

Die Demo verwendet ausschließlich synthetische Daten und FakeBroker in getrennten Datenbanken. Es wurden keine Anbieter kontaktiert, keine echten Schlüssel verwendet und keine Brokerorders gesendet. Die humanen Einrichtungsdialoge speichern geschützt und geben keine Schlüssel zurück; CONFIGURED bestätigt Speicherung, keine geprüfte Verbindung. Einrichtung aktiviert keinen Handel. Das zusätzliche Paperbudget setzt weder Brokerkontostand noch vorhandenen Ledger zurück. LIVE bleibt unerreichbar.

Nach Anmeldung: **Übersicht → Demo & Einrichtung → 100 USD → Mock neutral → Demo starten**. Die gefüllte synthetische Order erscheint im Demoabschnitt. [Bedienungsanleitung](../runbooks/dashboard-demo.md), [Alpaca-Einrichtung](../runbooks/alpaca-setup.md).

Issuezustände und Managedblock-Hashes werden mit Read-back in `docs/development/github-demo-issue-sync.json` geführt; geprüfte Branch-/Mergezuordnungen werden nach erfolgter Integration in `docs/development/git-demo-integrations.json` dokumentiert. Diese Evidenz behauptet keine zukünftigen Git- oder CI-Ergebnisse.
