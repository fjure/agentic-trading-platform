# PTV-23: Abschließende lokale Integrationsverifikation

Stand2026-10-18, Change `paper-trading-vertical-slice`, Issue#23, Branch `codex/ptv-23-final-acceptance`, verifizierte Basis `ecddaae574fcc6465af4b9abeb296f11d52842fd`. Abnahmeumfang: lokaler Fake-Durchstich und offline zertifizierte, technisch vorbereitete PAPER-Grenzen. Kein LIVE, kein echter Providerrequest oder Broker-/Jev-Schlüssel im Agentenkontext. Ein tatsächlicher menschlicher Paperlauf und dessen Anbieter-/Browservergleich bleiben separat **nicht ausgeführt**.

## Durchgängiger Akzeptanzfall

`tests/integration/test_acceptance.py` verwendet eine UUID-isolierte echte PostgreSQL-Datenbank und die bestehende ausdrücklich synthetische PTV-13-Fixture. Zwei geschlossene Kerzen, neutrale Evidenz, Mockentscheidung, unveränderlicher Vorschlag, normale atomare Riskreservation und genau ein Fake-Submit führen über Teil-/Endfill zum gebuchten Portfolio. Die Fixture prüft bereits Crashzustellung/Outboxdeduplizierung und die vollständige korrelierte Audit-/Referenzkette. Der neue Fall hält den tatsächlich verwendeten FakeBroker fest, beansprucht einen neuen Owner und durchläuft eine frische menschliche Resumeanforderung plus echte inventargebundene Recovery. Er bestätigt ein VALID/CURRENT-Portfolio, ohne Gates oder Reconciliationflags manuell freizugeben.

Ein echter FastAPI-ASGI-Testclient mit eingeschränkter Control-Rolle und synthetischer Testsession liest Snapshot sowie Markt-/Entscheidungs-/Vorschlags-/Risk-/Order-/Audithistorien derselben Trace-ID. Die öffentlich präsentierten IDs und Referenzen müssen zur tatsächlichen SQL-Kette gehören; Decision-Inputhash, Menge, Fillstatus und Auditaktionen werden geprüft. Die abschließende HTTP-Pause ist verbindlich und invalidiert erneut die Fortsetzungsautorität: Modusreconciliation wird korrekt PENDING, während bestätigte Fills und Ledgerbelege erhalten bleiben. Kein weiterer Submit.

Der Test injiziert eine fremde Tool-/Installationsanweisung als Evidenzquellenwert in den tatsächlichen MockDecisionEngine-/Plannerpfad. `os.system`, `subprocess.run` und `Popen` sind währenddessen Fallen: null Aufrufe. Der Text bleibt Dateninhalt, die echte neutrale Evidenz wird nicht zur Orderanweisung umgedeutet. Der Quellenwert wird in der öffentlichen Marktprojektion durch den bestehenden sicheren Quellentyp ersetzt.

Die18 Playwrightprüfungen bleiben eine eigene Browserstufe mit synthetischen vertraglichen Antworten. Der PG-Fall verwendet tatsächliche SQL-/HTTP-Komponenten und keine erfundenen HTTPantworten. Es wird kein einzelner durchgängiger Browserlauf gegen genau diese temporäre PG-Datenbank behauptet; die Abnahme verbindet überprüfte Schichten. Echte Börsenzeiten, Entitlements, Providerqualität und reale Paperfills werden damit nicht zertifiziert.

## Gefundene und behobene Integrationsfehler

Der echte HTTPpfad fand503 statt valider Vorschläge: der Planner speichert neben dem fachlichen Grund seine interne Konfigurationsversion in `rationale_codes`; die öffentliche Projektion behandelte jede Zeichenfolge als kanonischen Reasoncode. Jetzt wird der erste fachliche Grund weiterhin streng geprüft, nachfolgende bekannte Codes werden normalisiert/dedupliziert und private Provenienzwerte bleiben intern. Ein unbekannter erster Grund erhält ausdrücklich keinen erfundenen OK-Fallback.

Bestätigte Recovery-/Submitereignisse besitzen nun die eng belegten Mappings `BROKER_RECONCILED→OK`, `ACKNOWLEDGED→OK` und `SUBMIT_RESULT→OBSERVED`. Ein ungewisser Submit bleibt `BROKER_STATUS_UNKNOWN`; ein beobachtetes Ergebnis wird nicht pauschal ACCEPTED. Die tatsächliche Lab-Auditprüfung fand zusätzlich die bekannten technischen Initialisierungsaktionen: `runtime.initialize`/`risk.policy.initialize` werden ausschließlich als OBSERVED mit `SIMULATION_BOOTSTRAP→MODE_NOT_PAPER` präsentiert. Unbekannte private Auditaktionen bleiben abgewiesen. Kein generischer Unknown→OK-Pfad, keine Veränderung von Tradinggates, Ledger oder kanonischem Vertrag.

Die Projektionsregressionen prüfen normale Runtime-/Smoke-/Fixture-Planerversionen, private Canaries, unbekannten Pflichtgrund, bestätigte Recovery, ACK/UNKNOWN-Submitresultate sowie die geschlossene Fake-Initialisierung. Ein reales Lesen des öffentlichen Auditfeeds unter der laufenden Control-Rolle bestätigte9 präsentierte Ereignisse und unverändert DISABLED/PAUSE_NEW.

## Ausgeführte Abschlussprüfungen

| Gate | Tatsächliche Evidenz |
|---|---|
| Neue PG→HTTP-Abnahme |1passed/24.67s nach den ersten vollständigen Projektionskorrekturen; abschließend `pytest tests/contracts tests/integration/test_acceptance.py -q`:371passed/28.13s auf finalem Stand|
| Finale lokale Pythonmatrix |`pytest tests/unit tests/contracts tests/property tests/security tests/paper tests/integration/test_replay.py tests/integration/test_jev_shadow.py tests/recovery/test_alpaca_fixtures.py -q`:698passed/6separat abgedeckte Skips/78.71s; frühere Zwischenläufe werden nicht als zusätzliche unterschiedliche Tests summiert|
| Actual Deployment/Windows-Host |`PTV21_DOCKER=1 pytest tests/security/test_secrets.py tests/security/test_host_transport.py -q`:10passed/40.08s, tatsächliche Rollen/Mounts/Egress/Canary/Loopback/Host/Origin sowie Prozesscleanup|
| Linux-tmpfs-/Healthparallelität |`pytest tests/integration/test_failures.py -q -k api-concurrency`:1passed/8deselected/23.68s; deckt den Windows-Linuxskip tatsächlich ab|
| Finales Schema, Backup/Restore, Jobs/Outbox |`pytest tests/integration/test_ledger.py tests/integration/test_jobs_outbox.py -q`:3passed/76.77s unter Schema0011|
| Papergrenzen und Geld-/Cancel-/UNKNOWN-Fälle |Sechs unterschiedliche tatsächliche PostgreSQL-/MockTransport-Szenarien sowie62 lokale Operator-/Requestfälle im unmittelbar vorausgehenden PTV-22; fünf Produktionshashes unverändert, siehe `ptv-22-paper-smoke.md`|
| Wiederholbare UI-/Clientprüfung |`scripts/check-angular-client.ps1`: zwei bytegleiche67-Dateien-Generierungen, Übereinstimmung mit vorhandenem Client und TypeScript-/ABSTAINfixtures; ausschließlich temporärer Generatoroutput|
| Angular/Playwright |`scripts/test-dashboard.ps1`: Produktionsbuild bestanden,18passed/21.7s (16 Browser-/2 Projektionsfälle)|
| OAS/Schemas/Beispiele |`scripts/validate-contracts.py`: OAS3.1.2,20 Operationen,11 Schemas,10 interne Beispiele,13 HTTPantworten,4 Problems und Pflichtnegativfälle bestanden|
| Semantische Baseline und Lint |oasdiff1.33.0 gegen echte main-Basisecddaae: keine Änderungen; gepinntes Redocly2.62.1 validiert den Vertrag|
| Abhängigkeitsaudits |`pip_audit --local --skip-editable` und Dashboard-`npm audit`:0 bekannte Schwachstellen; ignorierte JSONreports unter `.tooling/output/ptv23-*-audit.json`|
| OpenSpec/Traceability |strict valide;40 ADDED Requirements/44 Szenarien,25 Leaf-Tasks, geordneter eindeutiger DAG;40 repräsentative Testpfade auf tatsächlich vorhandene Dateien abgeglichen|
| Ruff/Quellencredentialscan |Geänderte Pythondateien lintfrei;393 Git-relevante Textdateien,0 Credentialmuster; keine privaten Credentialdateien gelesen|

Die sechs Windows-/Profilskips der lokalen Pythonmatrix werden durch die tatsächlichen zehn Deployment-/Hostfälle und die separate Linuxparallelität abgedeckt. Frühere vollständige Geld-/Crash-/Reconciliation-/HTTP-/SSE-Matrizen stehen im Taskaudit und werden nicht als ein heute erneut ausgeführter einziger Gesamtlauf ausgegeben. Neuer beziehungsweise geänderter Produktionscode wurde gezielt und mit der abschließenden lokalen Suite verifiziert. API/UI laufen inzwischen mit den neu gebauten lokalen Images, getrennten Rollen und vorwärts migriertem Schema0011; keine Datenbank-/Volumelöschung und keine Provideraktivierung.

Bekannte nichtblockierende Toolwarnungen: OpenAPI3.1-Unterstützung des Generators bleibt upstream beta; Angular meldet621.07kB gegenüber500kB-Warnbudget sowie7.12kB CSS gegenüber4kB, harte1MB/8kB-Grenzen unverändert; bestehende Starlette/httpx-Testclient-Deprecation. Gepinnte Versionen wurden nicht eigenmächtig aktualisiert; die Redocly-Updateanzeige ist kein Updateauftrag. Keine Performance- oder ausschließlich stabile Transitivitätszusage.

## Requirement-/Szenarioabdeckung

`test-strategy.md` und daraus reproduzierbar abgeleitete `traceability.{md,json}` enthalten alle40 konkreten repräsentativen Einstiege, Requirement-/Task-/Design-/Contractreferenzen. Der statische `testStatus=planned-not-executed` bleibt eine Eigenschaft dieses Ableitungsvalidators; er ersetzt keinen Ausführungsreport. Tatsächliche Resultate stehen hier und in den Tasknachweisen.

| Exakte Requirementgruppe | Implementierung | Ausgeführte Tests und bisherige Detailnachweise |
|---|---|---|
| REQ-MKT-001..008 |`services/worker/market_state.py`, `services/execution/market_gateway.py`, `packages/decisions/neutral_evidence.py`, Alpaca-Marktdaten-/Quoteadapter|`test_market_state`, `test_decimal`, `test_alpaca_market`, `test_alpaca_quote`, neue PG-Abnahme; PTV-06/13/16/21/22|
| REQ-DEC-001..008 |`packages/decisions/{mock,baseline,jev,replay}.py`, `services/worker/planner.py`|`test_decision`, `test_jev`, `test_jev_wire`, `test_replay`, `test_jev_shadow`, neue PG-Abnahme; PTV-07/14/15|
| REQ-RSK-001..010 |`packages/risk/{engine,reservations}.py`, `services/execution/{gates,intents,reconciliation,paper_reconciliation,broker_binding,broker_recovery}.py`|`test_risk`, `test_risk_races`, `test_submit_crash`, `test_reconciliation`, `test_pause_resume`, `test_execution_isolation`, `test_alpaca_execution`, `test_alpaca_reconciliation`, `test_paper_smoke`; PTV-08..13/17/22/24/25|
| REQ-RUN-001..007 |`packages/persistence/`, `packages/observability/`, `services/worker/jobs.py`, getrennte `services/runtime/`-Prozesse/Migrationen|`test_ledger`, `test_jobs_outbox`, `test_failures`, `test_secrets`, `test_host_transport`, `test_runtime_telemetry`, neue PG-Abnahme inklusive Fremdinhaltsfalle; PTV-04/05/11..13/21/22/24/25|
| REQ-CTL-001..007 |`services/control_api/`, `apps/dashboard/src/app/{supervisor,state}/`|`test_control_api`, `test_control_http`, `test_control_auth`, `test_sse_cursor`, `test_supervisor_*`, `supervisor.spec.ts`, neue tatsächliche PG-/HTTPkette; PTV-18..21|

Die vier zusätzlichen Szenarien T-RSK-024/T-RUN-024 und T-RSK-025/T-RUN-025 sind durch die tatsächlichen dauerhaften Binding-/Activities-/Fee-/Restartmatrizen von PTV-24/25 und die nachgelagerten PTV-21/22-Fälle abgedeckt. Sie sind zusätzliche Szenarien derselben Requirements, keine vier erfundenen Requirements. Testfixtures und synthetische Sessions sind in den einzelnen Nachweisen ausdrücklich benannt.

## Unabhängiger Verify-/Safety-Review

Ein unabhängiger begrenzter Reviewer hat alle fünf Deltas, die40 Requirementzuordnungen, Design/Ports und die tatsächlichen Prüfberichte lesend geprüft. Die zunächst gefundenen Lücken (HTTP/SQL-Verbindung und direkte Fremdinhaltsfalle) wurden im neuen Akzeptanzfall geschlossen. Hauptagent und Testpaket führten die Tests aus; der Reviewer behauptet keine eigene Wiederholung. Nach dem letzten gezielten Mappingreview: keine verbleibenden bestätigten CRITICAL/P1/P2, keine offene Pflichtszenariolücke innerhalb des lokalen Abnahmeumfangs.

| Verify-Dimension | Ergebnis |
|---|---|
| Completeness |25/25 Tasks geprüft umgesetzt;40/40 Requirements implementiert und konkreten Tests zugeordnet; Status/Issueabschluss wird im selben Durchlauf vollständig zurückgelesen|
| Correctness |44 Szenarien durch konkrete Tests/Tasknachweise abgedeckt; echte PG-/HTTP-Abnahme und separate18 Browserprüfungen; keine erfundene providerseitige Aussage|
| Coherence |Sieben Ports, getrennte Rollen, Decimal/UTC, Contract-first, unveränderlicher Client, ursprüngliche Bootstraptexte als historisch eingeordnet; keine Implementierung außerhalb des autorisierten Umfangs|

Alle acht Safetyinvarianten sind innerhalb dieses Umfangs belegt: Worker ohne Submit-Port; deterministische letzte Gates/atomare Reservation; LIVE unerreichbar; DISABLED/unsicherer Neustart pausiert; Decimal/UTC; unabhängige Pause/Kill/Reconciliation; Execution-only-Secrets und Redaction; unveränderliche Referenzen/Audit/UNKNOWN-Hold. Teilfills, Cancel-/Fillrennen, Gebührenunsicherheit und kein Retry vor Reconciliation bleiben durch die gesonderten wirtschaftlichen SQLnachweise gedeckt.

## Betriebs- und Abschlussgrenzen

Das Fake-Lab bleibt unter `http://127.0.0.1:8000` DISABLED/PAUSE_NEW, ohne echte Broker-/Jev-Anbindung. Menschliche Aktivierung erfolgt nur im privaten Terminal; dieser Durchlauf erzeugt keinen echten Aktivierungscode. Der optionale echte Paperlauf benötigt die ausdrücklichen menschlichen Schritte in `docs/runbooks/paper-smoke.md` und ein passendes dediziertes Konto. Seine Nichtausführung ist ein ausdrücklich klassifizierter Ausschluss, kein fälschlich bestandenes Live-/Provider-Szenario.

OpenSpec-Sync der Deltas nach main-specs und Archivierung bleiben nach `tasks.md` einem ausdrücklichen Folgeauftrag vorbehalten. Dieser Abschluss archiviert oder synchronisiert keine Spezifikationen eigenmächtig. Issue-/Git-Read-backs und CI werden nach tatsächlicher Beobachtung in `codex-tooling.md`, `github-issue-sync.json` und `git-issue-integrations.json` nachgeführt.
