# Unabhängiger Planungsreview

30.10.2026; ausschließlich Propose. Zwei getrennte Reviewer mit frischem Kontext, read-only, keine Gitmutationen. Zusätzlich separater Review des begrenzten Issuesynchelpers. Hauptagent verantwortet Integration und Nachweise.

## Quant / Leakage / Reproduzierbarkeit

Reviewer quant_review las Proposal/Design/Methodik/Tasks/Traceability/Teststrategie, alle drei Deltas und relevante Bestandskomponenten. Ergebnis: keine P1/P2-Planungsblocker. EMA(2,3)- und Wilder-RSI(2)-Handwerte sowie Drawdown-/Return-/Varianzorakel unabhängig bestätigt. Validation-only-Auswahl, persistierte Inputhashes vor OOS, finale Auswahl ohne OOS, gemeinsame Seed-/Revisionbindung und Legacyhashschutz sind konsistent.

Übernommene Präzisierung: finale Holdout-Degradation explizit N/A NO_PAIRED_VALIDATION; nur foldweise gepaarte Val/Test-Tagesreihen liefern diese Kennzahl. Verbleibende bewusste Grenzen: Drawdown-first kann einen expliziten Flat-Suchkandidaten bevorzugen; endlich gesäte EMA hängt von gemeinsamem W ab; zwanzig Sitzungen und höchstens vier Folds ergeben keine statistische Gewissheit.

## Architektur / SQL / Worker / Safety

Reviewer safety_review las trading-safety-review/SKILL.md, Planungsartefakte und bestehende jobs.py, Worker, Migration0033/0034 sowie Container-/Privilegekonfiguration. Ergebnis: keine P1/P2-Planungsblocker. DBvalidierte Bindungen, Kompatibilitätsmarker, exakt altes Claim-SQL, gemeinsame gewichtete Admission, persistente Budgets/Fences und eng begrenzte Rechte passen zu den bestehenden Integrationsbarrieren. Taskgraph ist azyklisch mit klaren Dateizuständigkeiten.

| Invariante | Planungsbeleg / spätere Pflichtprüfung | Bewertung |
|---|---|---|
| Agent/Research ohne Broker-submit | REQ-QEX-006, D4/D7; QSL-09 echte Fähigkeitszähler/SQL42501/HBA | Plan erfüllt; neue Runtime unverified |
| Deterministische Risk-/Executiongates und LIVE unerreichbar | unveränderte Main-Spec risk-execution; keine neuen Tradingrechte; QSL-09 Bestandsregression | Plan erfüllt; keine neue Zertifizierung |
| DISABLED/fail-closed und sicherer Neustart | D4/D5/Migration Plan; Fence/Altworker-/frozen DISABLED-Smoke | Plan erfüllt; Applynachweis erforderlich |
| Decimal/UTC | D2 und metrics-methodology.md; unabhängige Orakel | Formeln geprüft; Implementierung ausstehend |
| Kill/PAUSE_NEW/Reconciliation unabhängig | gemeinsamer begrenzter Worker, QSL-09 echte volle Queue/CPU und5s-Fixture | Plan erfüllt; Lastwirkung unverified |
| Execution-only-Credentials/Redaktion | keine neuen Mounts/Netzwerke/Providerrechte; echte Canary-/Rollennegative | Plan erfüllt; neue Runtime unverified |
| Immutable Referenzen und Audit | D3/D4, Snapshots/Selections/Results/Exposition append-only | Plan erfüllt; SQLdurchsetzung ausstehend |
| Budgetreservierung/Expiry/UNKNOWN/Timeout/Teilfill/Cancelrennen | QSL verändert diese Tradingpfade nicht; relevante Paper-/Risk-/Reconciliationregression in QSL-09 | Bestandsanforderungen erhalten; keine Ausführung hier |
| Jevfehler blockiert keine Safetyoperation | keine Jevabhängigkeit im Researchworker; unabhängige Safety-/Reconciliationlastprobe | Plan erfüllt; tatsächliche Lastprobe in Apply |

## Testabdeckung und Issuegraph

test-strategy.md deckt alle12 Requirements und16 Szenarien ab. Neun Pakete mit lokalen Tests, Testevidenz und eigenem DoD; QSL-09 integriert bereits einzeln getestete Komponenten. Separater Reviewer runtime_discovery prüfte scripts/sync-quant-lab-issues-gh.py: keine zusätzlichen Blocker; nur Zielrepository/Issues, Managedblock-/Manuallabelerhalt, Stop bei Mehrdeutigkeit, getrennte native Add- und Vorwärts/Rückwärts-Read-back-Pässe. Tatsächliche Remoteprüfung steht in github-quant-lab-issue-sync.json.

Keine Runtime-/Marktzertifizierung: echte neue PostgreSQLgates, persistente Reservierung inklusive unbestätigter Crasharbeit, Speicher-/CPUgrenzen und Safetyunterlast müssen QSL-03/07/09 erst ausführen. Reale Datenrechte und historisches Vorwissen bleiben extern unverifiziert.
