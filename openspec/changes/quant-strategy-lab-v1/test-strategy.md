# Teststrategie

Dies ist ein Apply-Prüfplan; hier werden keine neuen Runtimeergebnisse behauptet. Jedes Paket liefert eigene Tests und docs/evidence/qsl-NN.md. Synthetische Signale/HTTPdoubles sind keine Marktvalidierung.

| Testgruppe | Positiv / negativ | Tatsächliche spätere Prüfumgebung |
|---|---|---|
| T-QSR-001/002 | Legacyhashorakel, unveränderte alte Idempotenzkeys, register fingerprint mismatch, bools/unknown keys/uploads | Unit, Contract, Registry-/Buildprüfung |
| T-QSR-003/004/005 | unabhängige EMA/RSI-Rechnungen, unveränderte Preise, Seed-/Fenstergrenzen, Fehlbars, Schwellenwerte exakt/knapp daneben, Targethysterese, Zukunftsperturbation, kein Seedtrade | Unit/Property, echter Datasetport beider Modi |
| T-QEX-001 | 3x3, kanonische Reihenfolge, Duplikate, 32/33, große saturierende Produkte, kein Job bei irgendeiner ungültigen Kombination | Unit/API und DBzeilenzähler |
| T-QEX-002 | gleiche Revisions-/Kalender-/Kostenbindungen, Korrektur zwischen Kandidaten, Restore/Retention, kein latest nach Restart | echte PostgreSQLrollen und parallele Commits |
| T-QEX-003/004 | Phasenüberlappung, Foldreset, Holdoutperturbation, eingefrorener Selectorinputhash, gleiche Ties, fehlender Kandidat blockiert Auswahl, alte Einzelruns/andere Hypothese gleicher Test | Unit/Property plus echte PG/Worker-Recovery |
| T-QEX-005/006 | gewichtete Queue100, 99/100/101, 32 Kandidaten plus Referenzen, Arbeitsgrenze99999/100000/100001, Byte12MiB-Grenzen, Lease/fence stale, exakt altes Claim-SQL, Crash an jedem atomaren Fortschritt, maximal3 Versuche, OOM | echte PG-/Container-/Recoverytests, kein SQLcheckout während CPUarbeit |
| T-QEX-007 | gleicher Backfill Strict blockiert und retrospektiv explizit explorativ, spätere negative Trust/Lizenz/Maßnahme, direkte SQLpublish-Negative | PG/API/Worker und isolierter Container ohne private Mounts/Netzwerk |
| T-QCO-001/002 | inkompatible Snapshot-/Kosten-/Methodenversion, Teilfehler; handgerechnete Metriken und N/A-Randfälle aus metrics-methodology.md | Unit plus persistierter echter Report |
| T-QCO-003 | alle neuen tatsächlichen Routes/Relaymethods, Auth/Origin/CSRF, stale Draft, fremde Cursor, payload conflict, alte Client/Manifestkompatibilität | vollständige Contractsuite; kanonischer Vertrag, oasdiff gegen Basis, zweifache Generatorausgabe bytegleich |
| T-QCO-004 | Katalog->Builder->9er-Grid->Run->Report->Reload/Pagination; Validation/OOS klar getrennt, Exposition, Sessionverlust und verspätete Antworten/Dialogpolls | echter Chromium, öffentliche API, PostgreSQL und Worker, lokale historische Doubles |

## Refinement: zusätzliche positive und negative Abnahme

| Testgruppe | Konkrete Akzeptanz | Umgebung in Apply |
|---|---|---|
| T-QEX-008/009, S1..S12 | Return0.12/DD0.08 schlägt0.005/0.01 unter Limit0.10; alternative Kapitalerhaltung umgekehrt; Flat gegen positiv/negativ, explizites Aktivitätsminimum, Grenze exakt/darunter/darüber inklusive vor Rundung0.1000000000004; alle ineligible -> keine Ersatzstrategie; ein FAILED -> kein verkleinertes Ranking; Sharpe N/A ohne Pflichtmetrikverlust; Identität bei jeder Policy-/Parameteränderung, Ties/Mediane/Foldgrenzen | unabhängige Decimal-/Rationalorakel, Unit/Property, Contracts und persistierte Workerentscheidung |
| T-QEX-010 | keine/leere/falsch geordnete Phase abweisen; NOT_TRAINABLE statt TRAINED_MODEL; In-Sample nur deskriptiv; Validation optimiert; identisches Protokoll gleiche Auswahl; alle Selections vor OOS; schlechter Holdout kein Tuning oder Wiederwahl; OOS-Perturbation ändert Selectionhash nicht | Unit/Property plus echte PG/Worker-Recovery/API |
| T-QEX-011 | neun geplante Kandidaten vs tatsächliche Statuszahlen/Referenzen; frühere Validation/Analyse macht neues OOS/Holdout bekannt und umgekehrt; neue Hypothese/Revision/Actor keine Frische; Fehlversuche/supersedes bleiben sichtbar, Retry zählt einmal; parallele Admission registriert eindeutigen Vorgängerstand; spätere Warnung ändert gebundenen Hash nicht | echtes PostgreSQL, API/Worker und UI |
| T-QCO-005 | vollständige barbasierte Investiertheit und Bruttoexposition, null Fills/Entry vs Fill, Teilpositionsgröße, offene Endposition, kein synthetischer Exit, wiederholte Wechsel/Gebühren/keine Tageszeitgewichtung; Nacht/Wochenende/Earlyclose/Fehlbars/Phasenreset; Nullnenner/ungültiges Ledger; exakte selectionValues vs zwölfstellige Anzeige; Warnung vs explizite Mindestaktivität vs Datenblocker | unabhängige numerische Methodikorakel, echte Simulation/Reports und Browser |
| Forschungsworkflow | zwölf Schritte aus research-protocol.md, neun EMAkandidaten plus Referenzen, geschlossene Policy/Version, eingefrorene Grundlage, Auswahl-/Nichtwahlzweig, getrennte OOS-/Holdoutanzeige, Aktivität/Exposition/Reliability/Grenzen; kurzer Test korrekt N/A und SMALL_FOLD_SAMPLE | QSL-09 echte PG/HTTP/Worker/Chromiumkette, budgetierte synthetische Daten |

Planungsvalidierung prüft Requirements/Tasks/Traceability und OpenSpec; sie ersetzt keinen dieser späteren Produkttests. Kein neuer statistischer Testframework, keine manuell editierten Clientdateien.

T-QEX-011 prüft zusätzlich Upgrade mit nichtleeren historischen Strict-/Retrospective-Runs (einschließlich FAILED/BLOCKED/Retry), UNKNOWN bei unrekonstruierbaren Phasen und gleichzeitig eintreffende Legacy-/QSLadmission. Registrierung und Vorgängerstand müssen über beide SQLports atomar sein; alte Writer ohne Guard werden abgewiesen. T-QCO-005 prüft die HALF_EVEN-Ledgergrenze mit Cash0.00000001 ausdrücklich als gültigen Simulatorfall sowie jede quant-activity-warnings-v1-Schwelle exakt und beidseitig.

## Integration und Regression (bestehende Pflichten)

QSL-09 verbindet die vorher bereits getesteten Pakete. Tatsächlicher Backtestworkercontainer: 0.5 CPU/256MiB/read-only/UID10001, nur backtest_db, eigene DBidentität; Broker-/Provider-/Jev-Fähigkeitszähler null. Echte verweigerte SQLrechte (42501), HBA-Negative, unveränderte nichtleere Finanzledgerfingerprints. Shared100er-Queue während echter Simulation; Pause/Kill/Reconciliation bleiben unter der bestehenden5s-Fixturegrenze, unabhängig vom Researchfortschritt. Keine absichtliche echte Brokerorder, keine echten Credentials.

Migrationsupgrade von0034, gebundene Daten wiederherstellen, exaktes Provisionierungsmanifest und alte Worker/Strict-/retrospektive Jobs prüfen. Produktionsimage frozen ohne Devabhängigkeiten; Netzloser DISABLED-Smoke. Bestehende BKT/RDR/RRB-/MDR-/Paper-/Risk-/Reconciliationregression nach betroffenen Bereichen, keine Wiederholung historischer Zahlen als neue Evidenz. Browserproduktionbuild und TypeScript; Source-/Credentialscan, OpenSpec strict, Issuegraph forward/reverse und idempotenter Sync. Unabhängige Quant-/Leakage-/Safetyreview ohne offene kritische Befunde. Performanceabnahme misst gültige Grenzfixtures und normale9er-Grids; kein Versprechen für jede maximale Datensatzkombination.
