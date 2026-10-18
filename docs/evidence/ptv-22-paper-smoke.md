# PTV-22: Begrenzter menschlicher Paper-Smoke

Stand2026-10-18, Change `paper-trading-vertical-slice`, Issue#22, Branch `codex/ptv-22-paper-smoke`, geprüfte Ausgangsbasis `8588b96f7b3bc69701db84f2b2bce84013aedd14`. Technische Vorbereitung und tatsächlicher menschlicher Providerlauf sind getrennt. Kein echter Providerrequest, keine Order, kein Credentialzugriff durch Entwicklungsagenten; der echte Paper-/UI-Vergleich bleibt nicht ausgeführt.

## Verhalten und Grenzen

Die getrennten Execution-/Worker-Einmalpfade benötigen explizite Flags, privates Linux-Vordergrundterminal und eingetippte laufgebundene Bestätigung. Vor privaten Konfigurationszugriffen werden öffentliche UUID-/Fingerprint-/Instrument-/Decimalgrenzen geprüft. Keine freie URL oder Credentialargumente. Execution prüft die konfigurierte Kontoidentität vor Providerkontakt; vorhandene feste Host-/Accountguards prüfen beim Zugriff weiter. Menge genau1 ganzer Aktienanteil als LIMIT-BUY, maximal1000USD einschließlich1%Cashpuffer, zusätzlich unveränderte normale Risk-/Portfolio-/Session-/TTL-/Owner-/Fencinggates.

Einmaliger dauerhafter Startbeleg, kontoweite Sessionadvisorylock und saubere Ausgangsinventur blockieren Run-Replay beziehungsweise parallele Operatorläufe. Der Mensch erteilt nach Vorbereitung über den vorhandenen privaten Kontrollsocket einen frischen ACTIVATE_PAPER-Prüfrequest. Execution bearbeitet nur dessen ID. Verifizierte Rohbars werden bereits bei PAUSE_NEW gespeichert, damit Recovery sie prüfen kann; unreconciliierte MarketStates bleiben gesperrt.

Migration0011 schützt alle `paper_smoke.*`-Auditaktionen vor jeder Rolle außer `atl_execution`. Worker liest ausschließlich die öffentliche `paper_smoke_authorizations`-Projektion; keine pauschalen Audit- oder privaten Brokerrechte. Die120Sekunden-Freigabe bindet Run, Konto-Fingerprint, State, Instrument, Limit und Budget als kanonischen SHA256. Der normale Mock-/Baselinepfad muss LONG liefern, andernfalls entsteht kein Vorschlag. LIMIT/ID werden vor Persistenz endgültig festgelegt; SQL-Advisorylock und unveränderliche Identitäten verhindern doppelte Entscheidungen/Vorschläge bei gleichem Run.

Execution prüft die gesamte konkrete State→Decision→Proposal→Risk→Reservation→Intent-Kette und ruft Submit genau einmal für diesen Intent auf. Keine normalen Queues, kein `tick`, keine erneute Aktivierung, kein Retry/Repricing/Market-Fallback/Flatten. Cleanup pausiert auch bei Fehler/Ctrl-C/EOF. Ein vor DELETE dauerhaft auditiertes CANCEL_PENDING samt Report/Outbox geht höchstens einem Cancelversuch voraus.204 ist kein terminaler Status. Kumulative Broker-/Activities-/Konto-/Positions-/Gebührenbelege entscheiden den Abschluss; Unklarheit bleibt UNKNOWN mit gehaltener Reservierung. Ein begrenzter Outboxpublisher im `execute`-Cleanup publiziert öffentliche Ereignisse ohne weitere Ausführung; Rückstau liefert einen festen Fehlercode.

## Tatsächlich ausgeführte Prüfungen

| Prüfung | Beobachtetes Ergebnis |
|---|---|
| `pytest tests/unit tests/contracts tests/security tests/paper tests/recovery/test_alpaca_fixtures.py -q` |621 bestanden,6 bestehende Umgebungs-/Linux-Skips,75.95s; bestehende Starlette-Testclient-Deprecation, kein Fehler|
| Finale Operator-/Request-Offlineprüfungen |`pytest tests/paper/test_smoke.py tests/unit/test_paper_smoke_plan.py -q`:62 bestanden,1.02s; davon46 Operatorfälle inklusive neun abschließend ergänzter execute-Cleanup-/Unsafeconfigfälle und16 Request-/Workerguards. Vor-I/O-Guards, Fingerprint, genau ein Submit, Pause/Cleanup bei RuntimeError/Ctrl-C, Cancel vor I/O, Rennen, Gebührenautorität, UNKNOWN und begrenzte Outbox|
| Erste vier echte PostgreSQL-Szenarien |4 bestanden,94.64s: Workerautorität/TTL/Digest/Rollen, Rohbar-Bootstrap mit echter Recovery, gezielter Risk-/Submit-/Cancel204→UNKNOWN-Pfad und Operatorabbruch vor Submit|
| Worker mit parallelem gleichem Run |gezieltes Szenario bestanden,23.48s, genau ein unveränderlicher Vorschlag/Outboxsatz|
| Start-/Replay-/Risk-/Modusguards |gezieltes PostgreSQL-Szenario bestanden,23.44s: getrennte ACTIVE/HELD_UNKNOWN-Sperre, falscher Modus/Safety und Wiederverwendung der Run-ID|
| Positiver autoritativer Cancelabschluss |gezieltes sechstes PostgreSQL-Szenario bestanden,24.19s: genau1POST/1DELETE, CANCELED/RELEASED, vollständiger ready-Receipt/CURRENT-Report, PAUSE_NEW ohne erneute Handelsautorität und öffentliche CANCELED-Publishevents|
| OpenSpec strict und Plan/Traceability |PASS,40 Requirements/25 Leaf-Tasks, eindeutiger geordneter DAG|
| Contract-/Beispielvalidierung und oasdiff gegen echte main-Basis |PASS: OAS3.1.2,20 Operationen,11 Schemas,10 interne Beispiele/13 HTTPantworten/4 Problems; keine HTTPänderung, oasdiff1.33.0|
| Ruff für die zehn geänderten Pythondateien |PASS|
| Abschließender Credentialmusterscan |391 Git-relevante Textdateien,0 Treffer; keine privaten Credentialdateien gelesen|

Die sechs unterschiedlichen PostgreSQL-Szenarien wurden teils gezielt nach Fixtureergänzungen ausgeführt; zwischen diesen Läufen änderten sich die nachstehend gehashten Produktionsdateien nicht. Der positive PG-Fall verwendet den echten `run()`-Kern und anschließend denselben tatsächlichen `publish_outbox()`-Cleanuphelper wie der `execute()`-Wrapper. Die sechs Skips betreffen bestehende Linux-tmpfs-, lokale Hostbridge- und laufende Labprofilprüfungen. Deren tatsächliche Docker-/Transportnachweise stehen separat in PTV-21; dieser Task ändert weder deren Code noch Deploymentgrenzen. Neue Operator-/SQLpflichtprüfungen werden nicht geskippt. Die PostgreSQL-Fixtures verwenden tatsächliche eingeschränkte Rollen und ausschließlich synthetische HTTPX-MockTransports. Für zeitunabhängige Session-/Datenprüfungen verwenden sie explizit synthetische Kalender-/Gateway-DTOs; keine Zertifizierung realer Börsenzeiten, Entitlements oder echter Kontodaten. DBs/Logins sind UUID-isoliert und werden gezielt aufgeräumt, bestehende Volumes bleiben erhalten.

## Prüfbasis

SHA256 der fünf Produktionsdateien, die zwischen den gezielten PostgreSQLläufen unverändert bleiben:

| Datei | SHA256 |
|---|---|
| `packages/domain/paper_smoke.py` |`209b35ae10456e5b001341442a96da0ab45bacc5aeb3ac9e696b4bb35fa2a041`|
| `services/worker/paper_smoke_plan.py` |`85c5278a0167e2d34a74fa4db99497ad0f820f8c7bc53e50d3b689cafbd24236`|
| `services/execution/paper_smoke.py` |`c1917f1149933d74d852c558d5a482685588463d5dbfc2979fa45473eaffa12e`|
| `services/runtime/execution.py` |`0a55ff3fa2b76bfd7ee9930bf08fc868e56b354ddb69651556a8e7c4891a24f9`|
| `services/control_api/migrations/versions/0011_paper_smoke_authority.py` |`b26c70e26a1757768e59b55feaba6b9ff3b8dbacf4b76cab8e1bfd52a1014b96`|

Kanonischer HTTP-Vertrag unverändert: `8e9c9011765aaabc53631ada0713e61a817008003d3b6d2c9ccdca2192c9dbdf`. Keine Änderung am generierten Angularclient oder neue Abhängigkeit. Mechanische Clientgenerierung/Angularbuild bleiben Pflicht der bestehenden Remote-CI; deren aktueller Lauf wird erst nach tatsächlicher Beobachtung als erfolgreich bezeichnet.

Unabhängige Aufgabenpakete hatten disjunkte Dateien: Execution-CLI/Offlineprüfungen, PostgreSQL-Fixtures und rein lesender Safety-Review; Hauptagent verantwortet gemeinsame Request-/Workerlogik, Migration, Integration, Dokumentation und Issue-/Gitmutation. Das größere Modell/hohe Reasoning war auf die finanzielle Sicherheitsgrenze begrenzt. Gefundene Probleme wurden behoben: Audit-Actor allein war fälschbar (jetzt DB-Rollenschutz), Rohbar-/Recoverykreis, falscher Runtrace beim Ordermutationsaudit (jetzt Ordertrace), unklare Cancelantworten und begrenztes Outboxcleanup. Der erste unabhängige Reviewer erreichte nach seinen Sourcebefunden ein Modellnutzungslimit; ein frischer begrenzter Abschlussreview mit dem kleineren Modell prüfte den finalen Stand und die tatsächlich berichtete Evidenz erneut, ohne verbleibende bestätigte P1/P2. Die Tests wurden vom Hauptagenten beziehungsweise Testpaket ausgeführt, vom Abschlussreviewer gelesen, nicht als eigene Testausführung ausgegeben.

| Safetyinvariante | Abschluss innerhalb dieses Pfads |
|---|---|
| Worker ohne Submit-Port |PASS: getrennte CLI/SQL-Rolle, private Tabellen gesperrt, gefälschte Freigabe abgewiesen|
| Deterministische Risk-/Submitgates |PASS: tatsächliche Reservation/Submit, exakte Referenzen und ein POST; keine Gateausnahme|
| LIVE unerreichbar |PASS: feste Konfigurations-/Provider-/Hostgrenzen und frühe negative Konfigurationsfälle|
| DISABLED/fail-closed/Restart |PASS: Startinventur, Run-Replay-/Risk-/Modusguards und Cleanup bei Fehler/Interrupt|
| Decimal/UTC |PASS: kanonische Requestgrenzen, bestehende Domain-/DBregeln und tatsächliche SQLereignisse|
| Unabhängige Pause/Kill |PASS: separater menschlicher Kontrollrequest, unabhängiger Watchdog, endlich PAUSE_NEW ohne neue Recoveryautorität|
| Execution-Secrets/Redaction |PASS: Vor-Konfigurationsguards, Fingerprint vor I/O, feste Fehlercodes/Canaries; tatsächliche Providerkeys werden nicht getestet|
| Immutable/Audit/Reservation/Reconciliation |PASS: SQL-Autorität, Digest/TTL/Replay, Ordertrace, vor-I/O Cancelbelege, UNKNOWN-Hold und autoritativer CANCELED-/RELEASED-Abschluss|

Die technische Taskvorbereitung ist vollständig geprüft. Sie ist keine Freigabe für Agentenorders oder LIVE und kein behaupteter menschlicher Paperlauf. Issue-/Gitabschluss wird erst nach dem verifizierten Read-back und tatsächlichem Push/Merge in den Tooling-/Gitzuordnungen dokumentiert.

Aktuelle offizielle Primärquellen und menschliche Schritte stehen in `docs/runbooks/paper-smoke.md`; Context7 und [Paperdokumentation](https://docs.alpaca.markets/us/docs/paper-trading)/[Cancelvertrag](https://docs.alpaca.markets/us/reference/deleteorderbyorderid-1) wurden am2026-10-18 erneut geprüft. Reale Providerqualität, menschlicher Paperlauf und dessen Browservergleich bleiben unverifiziert.
