# Unabhängiger Planungsreview nach fachlichem Refinement

30.10.2026, Ausgangs-HEAD bdb2ac7dd1f8a5e02183bf35276b1f63774af684. Zwei unabhängige Reviewer mit frischem Kontext, read-only; ausschließlich Planung, kein Apply. Der frühere Proposereview bleibt in Git nachvollziehbar und wird nicht als Prüfung dieses Refinements ausgegeben.

## Quant Research Review

Reviewer quant_refinement_review prüfte Policy/Methodik/Protokoll, Deltas, Tasks/Tests/Traceability und Simulator. Ein P2: zusätzlich verlangtes G=Equity-Cash widersprach wegen HALF_EVEN der gültigen Simulatorformel Equity=money(Cash+q*Close). Behoben: G=money(q*Close) separat; keine distributive Identität erzwingen. Konkretes positives Grenzorakel mit Cash0.00000001 ergänzt und nachgeprüft. Zweiter gemeinsamer Befund: S4 nannte noch gleiche Anzeige-/Selektionsquantisierung; jetzt explizit unquantisiertes DD0.1000000000004 über Limit0.1 trotz gleicher Anzeige.

Nachprüfung: keine P1/P2 im fachlichen Rechenkern. Return unter vorab festgelegtem DD-Limit, negative/Flat-Kandidaten, alle-Folds-Gates, unquantisierte selectionValues, Nichtwahl, Isolation und Warnungen sind konsistent. Unabhängige Decimal-/Bruchrechnung bestätigt Exposure37/190 und9510 Barschritte der technischen Neunerfixture. Review erkannte außerdem falsch zugeordnete Taskergänzungen: korrigiert auf exakte Task-ID, Kennzahlen06, Worker/API07, UI08, Gesamtabnahme09; abschließende gezielte Nachprüfung bestätigt QSL-06..09 und meldet keine offenen P1/P2.

## Architecture & Safety Review

Reviewer safety_refinement_review wandte trading-safety-review an und prüfte bestehende Modelle/Worker-/DBgrenzen. P2: research-protocol setzte test_exposures irrtümlich als vorhanden voraus. Behoben: neue additive Tabelle, Backfill bekannter Legacyrollen, UNKNOWN bei fehlender Althistorie, gemeinsame atomare Strict-/Retrospective-/QSLadmission inklusive direkter SQLports und fail-closed Writerguard vor QSLzulassung. QSL-03 owns storage.py; T-QEX-011 prüft nichtleeres Upgrade und konkurrierende Legacy-/QSLadmission. Gezielte Nachprüfung bestätigt Korrektur und keine offenen Architektur-/Safety-P1/P2. Gemeinsamer S4-Befund ebenfalls nachgeprüft.

| Invariante | Planungsbewertung / notwendiger Applynachweis |
|---|---|
| Research/Agent ohne Broker-submit, Credentials oder Finanzwrites | Plan erfüllt, REQ-QEX-006; echte Fähigkeitszähler/42501/HBA in09 ausstehend |
| Deterministische Risk-/Executiongates, LIVE unerreichbar | erhalten; Bestandsregression Pflicht, keine neue Runtimezertifizierung |
| DISABLED, fail-closed, sichere Wiederaufnahme | D4/D5, alte Writer/Fences/Restarttests; Runtime unverified |
| Decimal/UTC und Ledgeridentitäten | exakte Auswahlwerte, unabhängige Halbgeraden-/Grenzorakel, keine zusätzliche Ledgergeldrundung |
| Kill/PAUSE_NEW/Reconciliation unabhängig, Jevfehler folgenlos | unveränderte Rechte/Abhängigkeiten, echte5s-Unterlastprüfung09 ausstehend |
| Immutable Referenzen/Policy/Audit | alle Selektionen vor jedem OOS, append-only Register/Manifest; SQLdurchsetzung03/07 ausstehend |
| Budgetreservierungen, Expiry/UNKNOWN/Timeout/Teilfill/Cancelrennen | keine Änderung der Tradingpfade; relevante Regression09 bleibt Pflicht |
| Runtime-/Workerbudget | gemeinsame Queue100/Lease,295s/drei Versuche/12MiB unverändert; Lastnachweis nicht ausgeführt |
| API-/Legacykompatibilität | additive QSLfelder, alte Datenselektionspolicy/Hashes unverändert, kanonischer Vertrag erst Apply |

## Grenzen und Abdeckung

16 Requirements,21 benannte Capability-Szenarien sowie S1..S12 und numerische Methodenorakel, neun offene Implementierungspakete, unveränderter DAG mit18 Kanten. Diese Zählung beschreibt die Planung, keine ausgeführten Produkttests. Kleine IEX-Historie, endlicher EMAseed, Testvorkenntnis und adaptive Mehrfachsuche bleiben Einschränkungen; keine Signifikanz, Marktvalidität oder Handelsfreigabe behauptet.
