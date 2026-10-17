# Codex-Tooling: Bootstrap-Status

Stand: 2026-10-17 (Europe/Berlin); ursprüngliche Quellenprüfung 2026-10-12. **Definition of Ready erfüllt; Apply gesondert freigegeben, 22/25 Tasks abgeschlossen.**

## Aktueller Apply-Stand

PTV-25 abgeschlossen: vollständige paginierte Paper-Order-/Activities-/Positions-/Kontobelege, private unveränderliche normalisierte Belege mit Hash/Order-/Tracebindung, idempotente Fill-/FEE-/CFEE-Buchung, finanzielle Startbasis und ursprüngliches Budget vor I/O. Explizites unveränderliches Aktien-Paper-Gebührenprofil; fehlende Gebührenautorität bleibt unbekannt und geschlossen. Tatsächliche ACTIVATE_PAPER-/RESUME-Recovery mit Owner/Fencing, frischer Capabilitybindung und bestätigten Daten; keine blinden Wiederholungen. 468 lokale Tests (7.63s), 39 Fake-/Safetyregressionen (867.15s), neun Alpaca-Binding-/Crashregressionen (214.96s) und 23 unterschiedliche neue PostgreSQL-Szenarien bestanden: 22 im vollständigen Lauf (190.66s), korrigierter gezielter Terminal-/Restartfall 1 passed (23.55s). Produktionsquellhashes zwischen diesen Läufen unverändert. Ruff, OpenSpec strict, 40 Requirements/25 LeafTasks, 352 Dateien ohne Credentialmuster, OAS/zehn Schemas/Beispiele sowie zwei bytegleiche 65-Dateien-Clientläufe/TypeScript bestanden; HTTP unverändert. Begrenzte unabhängige Reviews und Hauptagenten-Abschlussreview mit geschlossenen Befunden. Keine echten Broker-/Jev-Requests oder Orders, keine Deploymentfreigabe. Evidenz: docs/evidence/ptv-25-paper-reconciliation.md.

PTV-24 Gitintegration am 2026-10-17: Issuebranch codex/ptv-24-alpaca-execution, Commit 005123eb9b28c85ef6cd5d74850bd7f21ee61231; regulärer Merge-main abcd034ff82c40f6c04a9d17eaed2ef05f1c99ff. Remote-SHAs und Ancestry aller 21 integrierten Issue-Branches (PTV-01..20 plus PTV-24) verifiziert. Issue #24 completed, vollständiger 25-Issue-Read-back und Wiederholung mit null Writes. Die CI des Implementierungsmerges läuft zum Dokumentationszeitpunkt; ihr Erfolg wird noch nicht behauptet. Spätere reine Evidenzcommits dürfen main weiterführen; Zuordnung in docs/development/git-issue-integrations.json.

Am 2026-10-17 hat der Nutzer die zuvor konkret vorgelegte Erweiterung bestätigt: PTV-24 dauerhafte Alpaca-Executionbindung und PTV-25 verbindliche SQL-Paper-Reconciliation/Gebührenbelege werden vor PTV-21 eingefügt. Bestehende Task-/Issue-IDs bleiben erhalten; die Gesamtzahl steigt auf25. Diese beiden Voraussetzungen waren in PTV-17 als offen dokumentiert; vorhandene Fake-SQL-Tests und das synthetische Adapterjournal erfüllen sie nicht. Planung/Traceability und idempotenter Issue-Sync werden vor der Umsetzung aktualisiert. Alpaca-Aktivierung bleibt bis zu vollständigen PTV-25-Belegen technisch gesperrt. Keine echten Brokerrequests oder Orders. PTV-24 ist offline geprüft und abgeschlossen; PTV-25 inzwischen abgeschlossen; als Nächstes PTV-21. PTV-24 abgeschlossen: dauerhafte private Alpaca-Konto-/Asset-/Sessionbindung, kanonische Trading-Capability mit separat geprüfter Marktversion, atomare Recoveryinvalidierung bei Erstbindung/Provider-/Versionswechsel, SQL-SUBMITTING vor I/O und Restore aller möglichen Versuche ohne zweiten POST. Alpaca-ACK bleibt PENDING/fee=null, normale Aktivierung bis PTV-25 gesperrt. 419 lokale Tests (7.05s), neun neue PostgreSQL-Szenarien (209.74s), zusätzlicher ACK-Fall (23.84s), 13 Fake-/Ledgerregressionen (309.54s), Budgetrace (24.64s) und 26 Safety-/Pause-/Resume-Prüfungen (556.45s) bestanden. Ruff, HTTP/zehn Schemas/Beispiele, unveränderter HTTP-Vertrag und zweifach bytegleicher 65-Dateien-Angularclient/TypeScript geprüft. Begrenzte gegenseitige Subagentenreviews und Hauptagenten-Abschlussreview mit behobenen Befunden; kein vollständiger späterer unabhängiger Review behauptet. Keine echten Broker-/Jev-Requests oder Orders. Evidenz: docs/evidence/ptv-24-alpaca-execution.md.

Umgebungsreparatur 2026-10-15: Docker Desktop4.84.0.234817 konnte wegen nicht zugreifbarer AF_UNIX-Runtime-Sockets für Inference und Secrets Engine nicht starten. Bei beendetem Docker wurden die ausschließlich leere Socketeinträge enthaltenden Ordner reversibel erhalten: `Docker/run.stale-20261015-b7687594`, `Docker/run.stale-20261015-2f828d55` und `docker-secrets-engine.stale-20261015-b34cae28`, jeweils unter dem bestehenden lokalen AppData-Verzeichnis. Danach antwortete Engine29.6.2 und der vorhandene Fake-PostgreSQL-Service wurde healthy. Kein Reset/Prune, keine Volume-/VHDX-/Credential-/Settingsänderung und kein Upgrade. Diagnose gab nur fest gefilterte Startup-Komponenten/Fehlerflags aus. [Docker-Fehlerbericht460](https://github.com/docker/desktop-feedback/issues/460) beschreibt den Workaround; der Maintainer nennt4.92+ als Fixstand. Ein erneuter Appstart unter4.84 kann erneut betroffen sein; keine Dauerreparatur behauptet.

Wrapper-Korrektur 2026-10-13: OpenSpec setzt projektbezogenes XDG_CONFIG_HOME und Telemetrie nur im privaten Kindprozess. Dadurch bleibt GH-Authentifizierung der aufrufenden Shell erhalten; OpenSpec strict, danach GH/fjure und vollständiger23-Issue-Read-back im selben Shellaufruf bestanden. CLI-Recorder verifiziert Remote-Daten eigenständig erneut. Risk-Sicherheitsreview durch Hauptagent; unabhängige Subagenten wegen Nutzungslimit ohne Abschlussresultat. Historische Abschnitte weiter unten sind keine aktuellen Statusbehauptungen.

Aktuell maßgeblich: Der Nutzer hat die installierte offizielle GH CLI als Alternative ausdrücklich freigegeben. gh2.100.0 authentifiziert fjure und bestätigt das Zielrepository/main; PTV-05/07 abgeschlossen, Issues#5/#7 über den eng begrenzten CLI-Pfad geschlossen. Einzel- und vollständiger23-Issue-Read-back prüfen Marker, Managedblocks, Titel, Labels, Dependencies, Status und SHA256; identische Wiederholung erzeugt0 Writes. Zweiundzwanzig Issues geschlossen, drei offen; 25-Issue-Read-back ist maßgeblich. Damit ist der frühere reine MCP-Transportblocker aufgehoben. CLI-Evidenz getrennt in github-issue-sync.json; MCP-Befunde darunter bleiben historische Evidenz. Scope und Read-back-Gates unverändert, siehe github-cli-sync.md. PTV-10 Execution-Gates geprüft:16 Sicherheits-/Recoverytests bestanden, Zusatzguards2passed undRiskregression1passed; unabhängiger Safetyreview geschlossen. PTV-11 Reconciliation geprüft:18 verschiedenePrüfungen plus33Fake/Riskregressionen bestanden; unabhängigerReview ohneBefund. PTV-12 Pause/Resume/Watchdog abgeschlossen: 21 unterschiedliche PG-Kontrollfälle, elf Submitregressionen, 23 Isolation-/Reconciliationregressionen und 109 Contract-/Domain-/Broker-/Riskprüfungen bestanden; unabhängiger Quellreview ohne offene Befunde. SafetyEvent erweitert den internen Vertrag auf neun Schemas; HTTP und der 65-Dateien-Client bleiben kompatibel und bytegleich. Details: docs/evidence/ptv-12-safety-controls.md. PTV-13 vollständiger lokaler Fake-Trade abgeschlossen: sechs neue PG-Szenarien, elf Submitregressionen, Cancel-/Fill-Race und 109 lokale Prüfungen bestanden. SUBMITTING-Report/Audit/Outbox jetzt atomar vor I/O; unabhängiger Abschlussreview ohne Befunde. Details: docs/evidence/ptv-13-vertical-fake.md. PTV-14 Replay abgeschlossen:19 Replaytests,128 kombinierte lokale Prüfungen,100 feste Cases mit drei stabilen Läufen;0 Baseline-/Mock-/Cacheabweichungen,100% sichere Enthaltung bei simulierten Pflichtfehlern. Cacheadmission/Modellbindung/TTL und Corpus-/Semantikvergleich unabhängig geprüft; Details docs/evidence/ptv-14-replay.md. PTV-15 Jev-Shadow abgeschlossen: offizielle Modell-/Wireversion, sichere Enthaltung, persistente Tokenpaare,100 synthetische Cases und unabhängigerReview; Evidenz docs/evidence/ptv-15-jev-shadow.md. Reale Anbieterqualität bleibt ungeprüft. PTV-16 read-only Alpaca-MarketData/Gateway abgeschlossen:268 lokale Prüfungen und5Executionisolationtests, unabhängigerReview geschlossen; Evidenz docs/evidence/ptv-16-alpaca-market.md. PTV-17 lokaler Alpaca-Paperbrokerport offline geprüft:298 lokale und8PG-Regressionsprüfungen grün; unabhängigerReview geschlossen. Evidenz docs/evidence/ptv-17-alpaca-paper.md. SQL-Paperintegration/Activities/Gebühren bleiben harte Voraussetzungen vor PTV21/22/23; BUYMarket wird abgewiesen, keine vollständige Paper-Laufzeitfreigabe. PTV-18 Session-/Control-/Policy-HTTPadapter abgeschlossen:365lokaleTests,2Control-PGfälle und24Ledger/Outbox/Safetyregressionen bestanden;10Schemas,65DateienClientdrift/TS/AngularBuild undunabhängigerReviewgeschlossen. Evidence docs/evidence/ptv-18-control-api.md. PTV-19 Snapshot/History/Cursor/SSE fertig:399lokaleTests,4Supervisor-PG- und10dauerhafteRegressionen bestanden; persistente7Tage-Cursor,RR/64bit-Anker,Sessionwiderruf/Heartbeat/Reset, unveränderter65Dateien-Client. Evidence docs/evidence/ptv-19-snapshot-sse.md. PTV-20 Angular-Material-Supervisor fertig:18Browser-/Projektionsfälle,399Regressionen,rekursiveDTO-Projektion,Tracepagination undInmemorySSEzustand geprüft. Evidenz docs/evidence/ptv-20-material-supervisor.md. Nächstes Paket PTV-21 Deploymentgrenzen/Failure-Injection; reale Berechtigungen bleiben ungeprüft. Details docs/evidence/ptv-11-reconciliation.md. Details docs/evidence/ptv-10-execution.md.

Aktualisierung 2026-10-13: Der Nutzer hat Issue-Branches, Commit/Push und anschließenden Merge nach main autorisiert; verbindlicher Ablauf in git-workflow.md und AGENTS.md. Bootstrap sowie PTV-01..20 jeweils auf eigenem Branch gepusht und in main integriert. PTV-20 b3a5ea7, Merge-main b235e61; zwanzig Remote-Issue-Branches und Ancestry verifiziert. PTV-19 386900f, Merge-main eab46ba; neunzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-18 687abe3, Merge-main dd0d69b; achtzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-17 2a1cecf, Merge-main e3a808e; siebzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-16 72d8a1c, Merge-maind79e10a; sechzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-15 ee0089f, Merge-main96e4b88; fünfzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-14 4841b11, Merge-main5cbf2ac; vierzehn Remote-Issue-Branches und Ancestry verifiziert. PTV-13 6d6e530, Merge-main 4eb0f11; dreizehn Remote-Issue-Branches und Ancestry verifiziert. PTV-12 3794d0f, Merge-main 0a83823; zwölf Remote-Issue-Branches und Ancestry verifiziert. PTV11 5d572f3f, Merge-main e9f889df; elfRemoteIssueBranches undAncestry verifiziert. PTV10 f3ff185d, Merge-main b38b9a2d; zehnRemoteIssueBranches mitAncestry nachgewiesen. PTV09 ae76ca73, Merge-main3d90857; vollständiger Remote-/Ancestrynachweis für neun Issue-Branches in git-issue-integrations.json. Remote-SHAs, main als Remote-HEAD und Commit-Erreichbarkeit geprüft; Evidenz: git-issue-integrations.json. Generierter Client bleibt bytegenau einschließlich Upstream-Whitespace, .gitattributes verhindert Normalisierungsdrift. Echte main-Vertragsbaseline durch oasdiff geprüft.

Der offizielle GitHub-MCP fehlt aktuell vollständig im Sitzungskatalog. Projektbezogener CLI-Check zeigt github enabled=true und bearer_token-Authentifizierungsschema, beweist aber keine dynamische Authentifizierung. Die sechs Issue-Abschlüsse und Read-backs darunter sind historische erfolgreiche Evidenz. PTV-05/07 lokal implementiert/reviewed: integrierte Suite **96 passed in 77.41s**, Ruff, OpenSpec strict, Planvalidator und Source-Scan grün. Beide Checkboxen/Issues bleiben bis zum offiziellen Sync offen, ihre Anwendungspakete uncommitted. Keine Ersatzmutationen über gh/REST und keine Globalregistrierung. Projekt-MCPs müssen in der Sitzung neu geladen werden; falls dafür private Anmeldung nötig ist, ausschließlich scripts/start-codex-with-github-pat.ps1 extern vom Nutzer bedienen, keine Credentials im Chat.

PTV-01..04, PTV-06 und PTV-08 abgeschlossen; sechs Issues geschlossen, 17 weitere offen. Letzter vollständiger MCP-Read-back 2026-10-12T21:38:35.148405Z; Marker/Titel/Labels/Managedblocks/Dependencies/SHA256 und Sourcecheckboxen geprüft. 43 integrierte Tests bestanden; Ledger/Restore einschließlich 532 Runtime-Tabellenprivilegien gegen PostgreSQL18 geprüft. PTV-06 mit16 Marktdaten-/Präzisionstests und PTV-08 mit23 FakeBroker-Contracttests geprüft. PTV-05 Jobleases/Outbox im unabhängigen Safetyreview; PTV-07 Mock/Baseline in Arbeit. Keine API-/Risk-/Executionruntime zertifiziert. Details in implementation-evidence.md und contract-validation.md.

PTV-01 Buildgrundlage bestanden: root pyproject/uv.lock, Angular22.2.2/Material22.2.2/TS6.0.2-Lock, lokales fake Composeprofil mit digestfixierten Python3.13.16-/PostgreSQL18.6-Images, DISABLED-Prüfprozess ohne Credentials. Cleaninstall, Wheel/Frontend-/Containerbuild,4Failclosedtests,Lint,Dependency- und redigierter Source-Scan erfolgreich. Privates synthetisches DB-Secret außerhalb Git; Verzeichnis vor Erzeugung und bestehende/neue Datei-DACL auf aktuellen Benutzer begrenzt, boolesch geprüft. Reviewbefund zur ursprünglichen ACLreihenfolge behoben; PostgreSQL zusätzlich nichtroot/read-only/cap_drop ALL. Keine TradingRuntime zertifiziert, kein Broker-/Jev-Zugriff. Vollständige Evidenz: openspec/changes/paper-trading-vertical-slice/implementation-evidence.md; ausführbare Buildkommandos in docs/runbooks/local-operation.md.

scripts/record-issue-sync.py akzeptiert geschlossene Issues ausschließlich passend zur abgeschlossenen Sourcecheckbox und vorhandenem Evidenceverweis. Eindeutige Marker, Titel und Dependencies werden ebenfalls gegen Source und offiziellen Read-back geprüft. Planungsabschluss und historische Formular-/Bootstraptexte darunter bleiben als damalige Evidenz erhalten; bei Widerspruch gilt dieser aktuelle Apply-Stand.

## Maßgeblicher Abschlussstand: Formularproblem behoben, 23 Issues verifiziert

Nach Nutzerneustart wurde `get_me` als fjure bestätigt und der zunächst leere Zielbestand geprüft. PTV-01 wurde tatsächlich als Issue #1 erstellt; danach weitere22 Tasks abhängigkeitsgeordnet als Issues #2 bis #23, jeweils nach Duplikatsuche und mit einzelnem MCP-Read-back. Repositoryweiter zweiter Abgleich bestätigt genau23 eindeutige Taskmarker, Titel, Pflichtlabels, verwaltete Inhalte, offene Zustände und verlinkte Abhängigkeiten;23 unverändert,0 Updates,0 Duplikate. Die SHA256-Zuordnung in `github-issue-sync.json` ist vollständig, Status `synchronized-read-back-verified`, keine Blocker/pending IDs. Letzter Read-back 2026-10-12T16:50:15Z; unabhängiger lokaler Review bestätigt alle23 Zuordnungen und Hashes.

Damit ist der dokumentierte direkte GitHub-MCP-Pfad mit `X-MCP-Features: mcp_apps_disable_form_deferral` dynamisch nachgewiesen; keine unsichtbare Formularübergabe mehr. Codex writes/prompt und15-Tool-Allowlist erhalten. Ausschließlich fjure/agentic-trading-platform; keine PR-/Kommentar-/Branch-/Workflow-Writes, kein Commit/Push. Change-Label statt Milestone, weil keine Milestone-Tools freigegeben sind. Alle Anwendungstasks/Issues offen. `scripts/record-issue-sync.py` speichert ausschließlich bereits MCP-verifizierte initiale Read-backs, SHA256 und Zuordnungen; das Script ruft keinen externen Dienst auf und ersetzt keine MCP-Mutation.

Lokale Evidenz bleibt gültig: OpenSpec strict,40 Requirements/23 Tasks/Traceability,11 Skills, OAS3.1.2/18 Operationen/8 Schemas/Beispiele, Redocly und65 bytegleiche Clientdateien plus Erfolgs-/ABSTAIN-Typfixtures. DoR-Checkliste jetzt PASS. Der nächste separate Implementierungsauftrag ist `$openspec-apply-change paper-trading-vertical-slice`; dieser Durchlauf implementiert keine Anwendung. Die folgenden Formular-/Blockerabschnitte sind historische Diagnoseevidenz und werden durch diesen Abschlussstand ersetzt.

### Formularproblem: offizielle Kompatibilitätskorrektur vorbereitet

Am 2026-10-12 wurde die exakte Servermeldung in GitHubs offizieller Implementierung (`issueWriteAwaitingFormResult`) wiedergefunden. [Upstream Issue 2823](https://github.com/github/github-mcp-server/issues/2823) beschreibt dasselbe fehlgeschlagene Formularbild auch am Standard-Hosted-Endpunkt; Berichte betreffen andere Clients und beweisen keinen identischen Codex-Clientfehler. Die [offizielle Serverkonfiguration](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md#mcp-apps) dokumentiert dafür `X-MCP-Features: mcp_apps_disable_form_deferral`: MCP-App-Ergebnisansichten bleiben verfügbar, Schreibtools nutzen den direkten offiziellen MCP-Toolpfad.

Dieser Header ist jetzt in `.codex/config.toml` und reproduzierbar in `scripts/bootstrap-codex.py` ergänzt. Genehmigte gezielte Dateimutationen außerhalb der Sandbox, anschließend effektive CLI-Inventur PASS: Header vorhanden, 15 Tools, `writes` plus beide `prompt`-Overrides und PAT-Umgebungsreferenz unverändert; übrige Projektserver unverändert. Keine globalen Einstellungen, Secrets, zusätzliche Tools oder externe Mutationen. Die optionalen GitHub-MCP-App-Formulare werden ersetzt; Codex-Freigaben werden nicht deaktiviert. Sicherheitsprüfung vergleicht geparste Konfiguration vor/nach der einzigen Headerergänzung.

**Noch nicht dynamisch verifiziert:** Die bereits laufende MCP-Verbindung muss die neue Konfiguration laden. Kein unterstütztes Reload-/Reconnect-Tool steht in diesem Sitzungskatalog bereit. Deshalb kein erneuter Write über eine nachweislich ungeklärte alte Verbindung. Nutzer kann Codex vollständig beenden und über den bestehenden privaten PAT-Launcher neu starten; der Agent startet keine zweite Hauptsession und liest keinen Token. Danach im selben Projekt/Chat `get_me`, Zielrepo/Issue-Marker prüfen, PTV-01 als echten autorisierten Task erstellen und per `issue_read` zurücklesen, danach restliche23 Tasks idempotent synchronisieren. Erst echter Write + Read-back bestätigen die Behebung. Mappingstatus `ui-compatibility-fix-configured-reload-pending` ist maßgeblich gegenüber den folgenden historischen Blockerbeschreibungen.

Change `paper-trading-vertical-slice` ist angelegt: Proposal, fünf Delta-Specs mit 40 Requirements, Design, 23 offene Leaf-Tasks, ADRs, Contracts/Beispiele, Traceability, Teststrategie und Runbooks. Keine Anwendung implementiert, kein Commit/Push, keine Broker-/Jev-Credentials oder Orders. Der temporäre Angular-Client dient nur der Vertragsprüfung.

Nachprüfung in diesem Durchlauf: `get_me` bestätigt `fjure`, Origin ausschließlich `fjure/agentic-trading-platform`, 15 GitHub-Tools, `list_branches` leer und `list_issues` leer. `python scripts/check-mcp-config.py` bestätigt alle drei Projektserver und GitHub `bearer_token`; alle Allowlisten und `writes` plus zusätzliche `prompt`-Regeln erhalten. Keine Credentials gelesen. `python scripts/validate-skills.py`: 11/11 PASS.

Die autorisierten Labels `openspec` und `change:paper-trading-vertical-slice` wurden tatsächlich durch `label_write/create` angelegt und mit `list_label` zurückgelesen (12 Labels insgesamt). **Issue-Schreibausführung ist nicht verifiziert:** Zwei Aufrufe für denselben noch nicht angelegten Task PTV-01 lieferten `isError:true` und meldeten ein interaktives Formular mit ausdrücklich noch nicht erstelltem Issue. Der Nutzer sieht kein Formular. Erneutes `list_issues` bestätigt 0 Issues. Das ist ein ungelöstes Tool-/UI-Gate, keine nachgewiesene PAT-Rechteverletzung und keine neue automatische Approval-Ablehnung. Keine blinden Wiederholungen, keine REST-/gh-Ersatzmutation und keine Änderung der Approval-Konfiguration zur Umgehung.

Mapping `docs/development/github-issue-sync.json`: `specification-created-issue-sync-ui-blocked`, `tasks: []`, alle 23 IDs als pending, kein erfundener Inhalts-Hash, Issue-Link oder erfolgreicher Post-write-Read-back. Nur Labels sind verifiziert. Milestone nicht verwendet: kein sicherer Milestone-Lese-/Schreibtool in der Allowlist; das Change-Label genügt dafür laut Originalauftrag.

Lokale Evidenz: `./scripts/openspec.ps1 validate paper-trading-vertical-slice --strict --json` PASS, keine Befunde. `python scripts/validate-plan.py --write` PASS: 40 Requirements besitzen Szenarien, Design-, Contract-, Task- und Testzuordnung; 23 IDs eindeutig, Dependencies vollständig topologisch geordnet. Aktuelle Contract-/Generatorresultate stehen in `contract-validation.md`; DoR-Checkliste und Blocker in `openspec/changes/paper-trading-vertical-slice/readiness.md`.

Finale Wiederaufnahme um 16:01 UTC: Auth fjure, weiterhin 0 Issues und 12 Labels inklusive beider Change-Labels. Alle lokalen Restarbeiten abgeschlossen: OpenAPI3.1.2 mit 18 Operationen, 8 Schemas, Erfolgs-/Ablehnungs-/Konflikt-/Ausfall-/UNKNOWN-Beispiele, zusätzliche echte ABSTAIN-Fixture, typgebundene Events und Negativprüfungen PASS. Redocly 2.62.1 ohne Warnungen; Generator7.26.0 zweimal 65 bytegleiche Dateien, Angular22.2.2/TS6.0.2 einschließlich beider Erfolgs-/Ausfall-Typfixtures PASS. Empirische Null-/Conditional-Grenze des Generators durch standardkonforme Vertragsschreibweise behoben, kein generierter Code manuell geändert. OpenSpec strict/Planvalidator/Skills erneut PASS. DoR bleibt ausschließlich wegen fehlendem Task-Issue-Sync offen.

Prüftooling ist ausschließlich lokal: `scripts/bootstrap-contract-tools.ps1`, `.tooling/contracts` mit npm-Lock/Redocly/AJV/Generator-Wrapper und Python-Prüfrequirements; geprüfte Temurin17-/Node22.23.3-ZIPs und GeneratorJAR mit festen SHA256 im ignorierten lokalen Cache. Typecheckabhängigkeiten sind Prüftooling, keine Dashboardimplementierung. Update erfordert neue offizielle Quellen-/Hashprüfung und gemeinsames Update von Locks/Scripts/Prüfdokumentation; Disable durch Nichtausführen der Prüfscripts, keine MCP-Freigabe nötig. Lokale Caches und Outputs sind nach Pfadprüfung entfernbar und über den Bootstrap wiederherstellbar. Root-openapitools.json als automatisch entstandenes redundantes Prüfartefakt entfernt; kanonische `.tooling/contracts/openapitools.json` erhalten. Keine globale Paket-/Konfigurationsänderung.

OpenSpec-Schemaauflösung scheiterte innerhalb der Windows-Sandbox trotz vorhandenem Paket; dieselbe `scripts/openspec.ps1`-CLI funktioniert mit genehmigtem Zugriff außerhalb der Sandbox. Keine Paketdatei/Schema-Prüfung verändert. Die vorhandene Pythoninstallation meldet innerhalb der Sandbox außerdem gelegentlich eine Pfadwarnung, Prüfungen können trotzdem erfolgreich sein; Exitcode und Ergebnis bleiben maßgeblich.

Der geplante Python-Paketgraph wurde per genehmigtem pip-Dry-Run außerhalb der Sandbox aufgelöst: 53 Pakete, keine Installation; Report nur `.tooling/output/vendor-baseline-pip-report.json`. Stabile OTel-SDK-Abhängigkeit zieht eine beta-markierte Semantic-Conventions-Transitive nach; dies ist in `technology-baseline.md` ausdrücklich dokumentiert und kein verschwiegener Stable-only-Nachweis.

### Reproduzierbare lokale Fortsetzung

```powershell
./scripts/openspec.ps1 validate paper-trading-vertical-slice --strict --json
python scripts/validate-skills.py
python scripts/validate-plan.py --write
./scripts/bootstrap-contract-tools.ps1
./.tooling/contracts/venv/Scripts/python.exe scripts/validate-contracts.py
npm --prefix .tooling/contracts run lint
./scripts/check-angular-client.ps1
```

Bei fehlenden ignorierten Contract-Prüfwerkzeugen zuerst den dokumentierten lokalen Bootstrap verwenden; Versionen und offizielle Download-/Hashbelege in `contract-validation.md`. Keine Anwendungstasks abhaken. Nach Behebung des Issue-Formulargates `$github-issue-sync paper-trading-vertical-slice` im selben Projekt fortsetzen: zuerst offene/geschlossene Issues und Marker erneut lesen, dann fehlende Tasks idempotent synchronisieren und vollständig zurücklesen. Kein Auth-Neustart erforderlich, solange MCP-Lesetests funktionieren; nur bei tatsächlich fehlender Auth den vorhandenen privaten Launcher nutzen. Nicht unverändert OAuth wiederholen.

Die Arbeit wurde mit begrenzten unabhängigen Subagentenpaketen durchgeführt: Quellen/Doku auf kleiner Modellstufe, Specs und Contracts auf Standardstufe mit mittlerem Reasoning; keine vollständige Chathistorie übertragen. Dateizuständigkeiten getrennt; zusätzlicher unabhängiger Contractreview fand und konkretisierte Pflichtfeld-, Cursor-, Session- und Zahlenbereichslücken vor dem Abschluss. Hauptagent integriert Prüfresultate. Kontextübergabe: `openspec/changes/paper-trading-vertical-slice/handoff.md`.

Offizielle OpenAI-MCP-Dokumentation am 2026-10-12 erneut gelesen: [MCP-Konfiguration](https://developers.openai.com/codex/mcp/) und [Konfigurationsreferenz](https://developers.openai.com/codex/config-reference/). Projekt-Allowlisten und Approval-Grenzen wurden geprüft, nicht verbreitert. Die folgenden Abschnitte sind **historische Bootstrap-Evidenz**; bei widersprüchlichem altem Pending-Status gilt der Fortsetzungsstand oben.

## Historische GitHub-MCP-Verifikation nach PAT-Neustart

Prüfung am 2026-10-12, dokumentiert um 10:30:47 UTC: alle 15 erwarteten GitHub-Tools sind im tatsächlichen Sitzungskatalog verfügbar. `get_me` bestätigt authentifizierten Zugriff als `fjure`; personenbezogene Profilfelder wurden nicht ausgegeben oder gespeichert. Lokaler Origin stimmt mit `https://github.com/fjure/agentic-trading-platform.git` überein.

Lesende offizielle MCP-Aufrufe ausschließlich für dieses Zielrepository erfolgreich:

- `list_branches`: leere Liste.
- `list_issues`: 0 Issues, keine weitere Seite.
- `list_label`: 10 vorhandene Labels; `openspec` und `change:paper-trading-vertical-slice` fehlen noch.
- `list_pull_requests` mit state `all`: leere Liste.
- `actions_list` / `list_workflows`: 0 Workflows.

Der Nutzer bestätigt ausdrücklich: Fine-grained PAT mit Only select repositories ausschließlich für `fjure/agentic-trading-platform`; Issues read/write, Contents/Pull requests/Actions read-only und keine weiteren Schreibrechte. Scope-Evidenz besteht aus dieser Nutzerbestätigung und erfolgreichen MCP-Leseaufrufen, nicht aus Credentialinspektion. Projektkonfiguration behält 13 lesende Tools plus ausschließlich `issue_write` und `label_write`; Default `writes`, beide Schreibtools zusätzlich `prompt`. Die Toolnamen begrenzen Owner-/Repo-Parameter nicht selbst; der bestätigte PAT-Scope und die Zielregel gelten weiterhin.

Authentifizierung, dynamische Toolverfügbarkeit und Zielrepository-Lesezugriff sind bestätigt. Kein Test-Issue oder Test-Label erzeugt. Die tatsächliche Ausführbarkeit der Schreibrechte wird erst bei den autorisierten produktiven Task-/Label-Synchronisierungen mit Read-back nachgewiesen. Das bisherige Auth-/Lade-Gate ist damit auf Basis der vorliegenden Evidenz geklärt; vollständige DoR bleibt wegen fehlender Spezifikation, Contracts, Validierung und Issue-Synchronisierung unerfüllt. Keine Tokenwerte, Prozessumgebungen oder Credentialdateien gelesen; keine globale MCP-Änderung, kein Commit/Push und keine Anwendungsimplementierung. Die folgenden Anmelde-/Fehlerabschnitte beschreiben historische Schritte.

## Agentische Entwicklung und Tokenbudget

Nutzeranforderung vom 2026-10-12: Auch die Entwicklung der Anwendung soll agentisch mit geeigneten Subagenten erfolgen. Tokensparsamkeit hat hohe Priorität. Dieser Arbeitsmodus setzt bei Anwendungsimplementierung weiterhin das bestehende DoR-/GitHub-Gate voraus. Dauerhafte Regeln stehen in `AGENTS.md`; wesentliche Implementierungen erhalten weiterhin einen OpenSpec-Change.

- Hauptagent delegiert begrenzte Pakete mit Ziel, relevanten Requirement-/Task-IDs, Dateizuständigkeit, Akzeptanz und erforderlichen Checks. Subagenten implementieren, testen oder reviewen unabhängige Pakete; Hauptagent prüft und integriert Ergebnisse. Keine konkurrierenden Änderungen derselben Dateien oder pauschale Delegation kleiner Aufgaben.
- Nur relevante Regeln und Dateiausschnitte weitergeben, möglichst frischen Subagenten-Kontext verwenden. Ergebnisse enthalten kurz Änderungen, Pfade, Checks und offene Punkte. Gezielt suchen, Ausgaben begrenzen und vorhandene Evidenz wiederverwenden; zusätzliche Agenten nur bei erkennbarem Nutzen, da Delegation selbst Tokens kostet.
- Modellstufen nach Aufgabe auswählen: klein/schnell für begrenzte Suche und einfache Dokumentation; Standard für Implementierung und Tests; stärkste geeignete Stufe für schwierige Architektur, ungelöste Fehler oder sicherheitskritische Reviews. Reasoning ebenso bedarfsgerecht wählen. Nur tatsächlich angebotene Modelle verwenden; Namen und Preise sind keine dauerhaften Regeln. Bei mangelnder Qualität gezielt eskalieren und den Grund kurz festhalten.
- Contexting erfolgt bedarfsgerecht aus OpenSpec, `AGENTS.md` und relevanten Dateien. Automatische Kontextverdichtung der Laufzeit nutzen, soweit vorhanden. Vor absehbarem Kontextverlust oder Sessionwechsel einen kurzen Übergabestand in der jeweiligen Change-Dokumentation, bis dahin hier, sichern: Ziel, Scope/Freigaben, Change/Task, Dateistand, Entscheidungen, Check-Evidenz, Blocker und nächster Schritt. Keine Secrets, vollständigen Logs oder Chatkopien speichern. Nach Wiederaufnahme Dateien und Taskstatus erneut prüfen.
- Bei unübersichtlichem Kontext einen frischen Subagenten mit diesem Übergabepaket nutzen. Neue Hauptsessions nur über unterstützte und autorisierte Mechanismen; in dieser Desktop-Sitzung setzt das Anlegen eines neuen Chats einen ausdrücklichen Nutzerauftrag voraus. Automatische Compaction garantiert keinen automatischen Sessionwechsel. Fehlende Fähigkeiten offen benennen; weder DoR noch Rechte erweitern.

Für diese Dokumentationsergänzung wurde ein Subagent mit frischem Kontext und der verfügbaren kleinen Modellstufe `gpt-6-luna`, Reasoning `low`, zur begrenzten Regelprüfung eingesetzt. Anwendungscode, MCP-Konfiguration und OpenSpec-Tasks gehören nicht zum Umfang dieser Ergänzung; dafür ist kein Issue-Sync erforderlich.

Offizielle Grundlagen, abgerufen am 2026-10-12: [Subagenten mit getrenntem Kontext und klaren unabhängigen Aufgaben](https://developers.openai.com/api/docs/guides/agents-api/multi-agent) und [zusätzlicher Tokenverbrauch sowie API-spezifische automatische Compaction](https://developers.openai.com/api/docs/guides/responses-multi-agent). Diese API-Eigenschaften belegen keine identischen Desktop-Einstellungen; die Modellstufen oben sind Projektregeln.

## Nachprüfung in der neuen Sitzung (2026-10-12)

### Anmeldeversuch nach ausdrücklicher Nutzerfreigabe

Der Nutzer hat anschließend bestätigt, dass der PAT erstellt ist. Tokenwert und tatsächliche Scope-Einstellungen wurden nicht eingesehen. `scripts/start-codex-with-github-pat.ps1` bereitet die private Eingabe und einen App-Neustart vor: in einem eigenen externen PowerShell-Fenster ausführen, Codex selbst vollständig beenden, PAT mit `Read-Host -AsSecureString` eingeben. Der Helper übergibt ihn ausschließlich über `ProcessStartInfo.EnvironmentVariables` an den neuen App-Prozess; keine persistente Umgebungsvariable, globale Konfiguration oder Credentialdatei. Die neue App und ihre Kindprozesse erhalten die Prozessvariable; deren Umgebung darf nicht ausgegeben oder protokolliert werden. Die unverwaltete Eingabekopie wird freigegeben; das Betriebssystem benötigt den Bearerwert während der App-Laufzeit im Prozessspeicher. Bei einem späteren vollständigen Neustart ist die private Eingabe erneut erforderlich.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Users\Domenik\ai-projects\agentic-trading-platform\scripts\start-codex-with-github-pat.ps1'
```

`ExecutionPolicy Bypass` gilt nur für diesen gestarteten PowerShell-Prozess und ändert keine dauerhafte Policy; übergeordnete verwaltete Policies bleiben maßgeblich. Helper ausschließlich durch den Nutzer starten, niemals über Agenten-Tools mit Tokeninput. Er prüft den installierten `OpenAI.Codex`-Paketpfad und bricht vor Tokeninput ab, wenn die App/der Code-Host noch läuft. Nach Neustart dasselbe Projekt/diesen Chat öffnen und GitHub MCP verifizieren lassen. Programmpfad der laufenden App wurde ohne Prozessumgebung geprüft; PowerShell-Parserprüfung PASS. Helper nicht mit einem echten Token ausgeführt; direkte App-Aktivierung und MCP-Vererbung müssen nach Nutzerneustart noch bestätigt werden.

Offizielle technische Grundlagen: [Microsoft ProcessStartInfo.EnvironmentVariables](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.environmentvariables) und [OpenAI MCP-Bearer-Umgebungsreferenz](https://developers.openai.com/codex/mcp/), geprüft am 2026-10-12.

Der Nutzer hat anschließend den Beginn der Anmeldung autorisiert. Der unten dokumentierte `codex mcp login github`-Aufruf mit temporärem URL-Override und `mcp_oauth_credentials_store="keyring"` wurde ausgeführt (CLI 0.155.1). Ergebnis: Exit 1, `Dynamic client registration not supported`. Der Versuch scheiterte vor dem Browser-Freigabeschritt; keine erfolgreiche Authentifizierung. Dies ist ein technischer Fehler, keine neue automatische Approval-Ablehnung. Keine globale MCP-Registrierung und kein Wechsel zu Klartextspeicherung.

Als Vorbereitung des offiziellen PAT-Wegs wurde ausschließlich `bearer_token_env_var = "GITHUB_PAT_TOKEN"` im bestehenden Projekt-GitHub-Block und reproduzierbar in `scripts/bootstrap-codex.py` ergänzt. Keine Tokenwerte gesetzt, gelesen oder gespeichert; Allowlisten und Approval-Regeln erhalten. Der Nutzer muss den Fine-grained PAT selbst erstellen und sicher in die App-Prozessumgebung injizieren. Die Referenz allein bestätigt keine Authentifizierung. GitHub-MCP-Read-back und Repositoryrechte bleiben blockierend.

### Prüfungen vor dem Anmeldeversuch

- Context7 ist dynamisch verfügbar: `resolve_library_id` für Codex liefert `/openai/codex`; `query_docs` liefert Dokumentation mit offiziellen Quellverweisen. Kein API-Key verwendet.
- Playwright ist dynamisch verfügbar: genau die zwölf konfigurierten Browsertools stehen im Sitzungskatalog. `browser_navigate` auf `about:blank`, `browser_snapshot` und `browser_close` erfolgreich; keine externen Seiten oder persönlichen Profile geöffnet.
- GitHub fehlt weiterhin im tatsächlich verfügbaren Toolkatalog. Kein authentifizierter GitHub-MCP-Aufruf möglich.
- Codex CLI 0.155.1: `python scripts/check-mcp-config.py` außerhalb der Sandbox erfolgreich; drei aktivierte Server, GitHub `not_logged_in`, keine Bearer-Umgebungsreferenz. `codex mcp get github --json` mit temporärem URL-Override bestätigt die vorhandene Allowlist. Keine Registrierung oder Anmeldung ausgeführt.
- Ohne Credentials ausgeführtes HTTP-GET auf den offiziellen GitHub-MCP-Endpunkt liefert 401 und `No access token was provided in this request`. Die angekündigten öffentlichen Resource-Metadaten nennen `https://github.com/login/oauth` als Authorization Server. Das beweist weder einen erfolgreichen Codex-OAuth-Login noch dessen Repositorygrenze.
- `python scripts/validate-skills.py`: erneut 11/11 PASS; alle elf Skills stehen im Sitzungskatalog.
- Die aktuelle offizielle OpenAI-Dokumentation bestätigt Projektkonfiguration in vertrauenswürdigen Projekten, Tool-Allowlisten und Approval-Modi. Eine globale Registrierung ist nicht erforderlich. Die nun erfolgreichen Context7-/Playwright-Aufrufe widerlegen einen weiterhin allgemeinen Ladefehler aller drei MCPs. Fehlende GitHub-Authentifizierung ist nachgewiesen; eine zusätzliche Desktop-Ladeursache für GitHub ist noch nicht ausgeschlossen.
- Origin bleibt `https://github.com/fjure/agentic-trading-platform.git`. Dies ist lokale Remote-Evidenz, keine authentifizierte Prüfung von Repositoryidentität oder Berechtigungen. Der bisherige leere Remote-Check wurde nicht als neuer MCP-Read-back ausgegeben.
- Definition of Ready bleibt unerfüllt. Keine Change-Artefakte, Contracts, Issues oder Anwendung erstellt; kein Commit/Push.

### Historische Prüfung der vorherigen Sitzung

- In der fortgesetzten Sitzung sind keine Context7-, Playwright- oder GitHub-MCP-Tools im tatsächlich verfügbaren Toolkatalog vorhanden. Neustart allein hat die projektlokalen Server hier nicht verfügbar gemacht.
- Alle elf Repository-Skills bestehen die statische Prüfung und sind im Sitzungskatalog aufgeführt.
- Read-only `codex mcp list --json` außerhalb der Sandbox listet weiterhin nur die vorhandenen benutzerweiten Server.
- `python scripts/check-mcp-config.py` mit expliziten projektbezogenen Overrides erkennt alle drei Server; GitHub meldet jetzt eindeutig `not_logged_in`.
- Innerhalb der Sandbox scheitert die CLI-Inventur am Canonicalize-Zugriff auf CODEX_HOME. Die lesende Prüfung außerhalb der Sandbox war erfolgreich; keine Konfiguration und keine Anmeldung wurden verändert.
- Projektvertrauen ist vorhanden; die Ursache, warum die Desktop-Sitzung die lokale MCP-Konfiguration nicht als Tools bereitstellt, ist noch nicht nachgewiesen. Ein neuer Chat im selben Projekt kann als nächster Prüfschritt dienen, ist aber keine bestätigte Lösung.
- GitHub-Authentifizierung und dynamische Toolfreigabe bleiben blockierend. Keine Issues erstellt und keine Spezifikations-/Anwendungsarbeit über dieses Gate hinaus ausgeführt.

Der vollständige Originalauftrag liegt in `Codex-OpenSpec-Prompt-Trading-Bot.md`.
Sein GitHub-Abschnitt verlangt, ohne funktionsfähigen offiziellen GitHub MCP nicht weiterzuarbeiten.
Deshalb ist die Arbeit am Tooling-Gate angehalten; Change-Artefakte, API-Verträge und Anwendungscode wurden noch nicht erzeugt.
Die geplante Change-ID ist `paper-trading-vertical-slice`, noch kein existierender validierter Change.
Nach Nutzerentscheidung werden alle drei MCPs ausschließlich projektbezogen eingerichtet. Eine benutzerweite MCP-Registrierung ist nicht erforderlich und wird nicht weiter verfolgt.

## Bestandsaufnahme und Ziel

- Ursprünglich nur der Markdown-Prompt; keine Architektur, Anwendung, Git-Metadaten, AGENTS.md oder OpenSpec-Installation.
- Keine übergeordneten AGENTS.md an den geprüften Pfaden C:/, C:/Users/, C:/Users/Domenik/ und C:/Users/Domenik/ai-projects/ gefunden.
- Nutzer nennt ausdrücklich `https://github.com/fjure/agentic-trading-platform.git`.
- Ein Root-Git-Repository auf `main` initialisiert, `origin` gesetzt. Kein verschachteltes Repository, Commit oder Push.
- `git ls-remote https://github.com/fjure/agentic-trading-platform.git`: erfolgreich (Exit 0), keine Referenzen; Remote derzeit leer. Authentifizierter MCP-Read-back von Repositoryidentität und Berechtigungen steht noch aus.
- Greenfield-Projektname gemäß Prompt: `agentic-trading-lab`; GitHub-Repositoryname bleibt `agentic-trading-platform`.
- Node 22.18.0, npm 10.9.3, Codex CLI 0.155.1, Python 3.13.6, Git 2.45.1.windows.1, Docker CLI 29.6.2. Docker-Daemon nicht geprüft.
- Vorhandene globale MCPs/Plugins wurden inventarisiert, aber weder entfernt noch umkonfiguriert.

## Installation und Versionen

Offizielle npm-Registry mit `npm view <package> version engines repository --json --offline=false` geprüft.
Lokale Tooling-Installation unter `.tooling/`; direkte Versionen exakt gepinnt, Transitive über `.tooling/package-lock.json` gesperrt.
Keine Anwendungspakete und keine Broker-/Jev-SDKs installiert.

```powershell
npm ci --prefix .tooling --offline=false
./scripts/bootstrap-openspec.ps1
# Auf einem frischen Checkout ohne diese Dateien:
python scripts/bootstrap-codex.py
```

Der Codex-Bootstrap verweigert das Überschreiben bestehender Projekt-Skills/MCP-Konfiguration; Anpassungen vorhandener Dateien werden separat reviewed.
Die Scripts enthalten nur Entwicklungsinfrastruktur und keine Trading-, Broker- oder Anwendungslogik.

| Skill / MCP | Version | Scope | Workflow / freigegebene Tools | Verifikation / Laden |
|---|---|---|---|---|
| openspec-explore | generiert mit 1.14.1 | .agents/skills | explore | YAML/Metadaten PASS; im Sitzungskatalog erkannt |
| openspec-propose | generiert mit 1.14.1 | .agents/skills | propose | YAML/Metadaten PASS; im Sitzungskatalog erkannt |
| openspec-apply-change | generiert mit 1.14.1 | .agents/skills | apply | YAML/Metadaten PASS; Implementierung blockiert |
| openspec-verify-change | generiert mit 1.14.1 | .agents/skills | verify | YAML/Metadaten PASS; keine Implementierung zur Prüfung |
| openspec-sync-specs | generiert mit 1.14.1 | .agents/skills | sync | YAML/Metadaten PASS; im Sitzungskatalog erkannt |
| openspec-archive-change | generiert mit 1.14.1 | .agents/skills | archive | YAML/Metadaten PASS; kein Change archivierbar |
| api-contract-workflow | initial, Git-Arbeitsstand | .agents/skills | Contract-first CLI-Workflow | skill-creator quick_validate PASS |
| trading-safety-review | initial, Git-Arbeitsstand | .agents/skills | Sicherheitsreview mit Evidenz | quick_validate PASS |
| broker-adapter-certification | initial, Git-Arbeitsstand | .agents/skills | Capability-/Recovery-Zertifizierung | quick_validate PASS |
| decision-engine-evaluation | initial, Git-Arbeitsstand | .agents/skills | Replay-/Shadow-Evaluation | quick_validate PASS |
| github-issue-sync | initial, Git-Arbeitsstand | .agents/skills | idempotente Issue-Synchronisierung | quick_validate PASS; Auth/Sync blockiert |
| Context7 | @upstash/context7-mcp 4.3.0 | Paket lokal, Projektkonfiguration | resolve-library-id, query-docs | dynamischer Resolve-/Query-Smoke PASS; ohne Key |
| Playwright MCP | @playwright/mcp 0.0.83 | Paket lokal, Projektkonfiguration | 12 Tools, siehe unten | dynamisch about:blank/Snapshot/Close PASS |
| GitHub MCP | offizieller hosted Service, serverseitig unpinnbar | Projektkonfiguration | 13 lesende + issue_write/label_write | 15 Tools dynamisch erkannt; Auth/Repository-Lesetests PASS; PAT-Scope nutzerbestätigt; Schreib-Read-back beim Task-Sync ausstehend |

OpenSpec-Profil ist `custom` mit `explore, propose, apply, verify, sync, archive`, Delivery `skills`.
Die aktuelle CLI hat kein nichtinteraktives Preset namens `expanded`; die geforderten Workflows sind explizit aktiviert.
`openspec update` erfolgreich; die optionale Warnung zum nicht ausgewählten Core-Workflow `update` ist kein Fehler.
Das Profil liegt in `.tooling/config/openspec/config.json`, auf diesen Projektpfad isoliert über `XDG_CONFIG_HOME`.
`./scripts/openspec.ps1` setzt zusätzlich `OPENSPEC_TELEMETRY=0` und `DO_NOT_TRACK=1`.
`.tooling/config/` ist ignoriert und durch `bootstrap-openspec.ps1` reproduzierbar.

Der generische skill-creator-Validator beanstandet bei den sechs unveränderten offiziellen OpenSpec-Skills das zusätzliche `compatibility`-Feld.
Die offiziellen Dateien wurden nicht verändert. `python scripts/validate-skills.py` prüft deshalb zusätzlich YAML, erforderliche name/description, Pfadübereinstimmung und eindeutige Namen: **11/11 PASS**.
Alle elf Skills sind inzwischen auch im Sitzungskatalog erkannt. Dies bestätigt die Erkennung, nicht die Ausführung jedes Workflows.

Hinweis zu Transitivität: Microsofts stabiles MCP-Paket 0.0.83 pinnt intern Playwright/Playwright-core `1.64.0-alpha-1790635538000`.
Das ist eine dokumentierte Upstream-Einschränkung; es wurde kein selbst ausgewähltes Alpha-MCP installiert. Vor einer produktiven Freigabe ist die Stable-only-Anforderung einschließlich dieser Transitive erneut zu entscheiden.

## Konfiguration und minimale Berechtigungen

Kanonische secretfreie Projektkonfiguration: `.codex/config.toml`, erzeugt durch `scripts/bootstrap-codex.py`.
Die offizielle Konfigurationsdokumentation bestätigt projektlokale MCPs für vertrauenswürdige Projekte. Globale MCP-Registrierung ist keine Voraussetzung. Die Einschränkung des Verwaltungsbefehls wird durch dokumentierte, nur für den jeweiligen Prüfprozess geltende CLI-Overrides behandelt.
Die absoluten lokalen Paketpfade vermeiden ungepinnte npx-Downloads beim Start.
Codex wurde für genau diesen Projektpfad als trusted registriert, ohne andere Projekteinstellungen zu verändern.
Trotzdem zeigt `codex mcp list` der installierten CLI hier nur benutzerweite Einträge; die projektlokalen Einträge werden bei diesem Verwaltungsbefehl nicht geladen.
Context7, Playwright und GitHub sind inzwischen dynamisch verifiziert; aktuelle GitHub-Evidenz steht am Anfang dieser Dokumentation.

Read-only-Verifikation der offiziellen CLI mit expliziten `-c`-Overrides:

```powershell
python scripts/check-mcp-config.py
```

Historisches Ergebnis vor PAT-Neustart: alle drei Server sichtbar; GitHub `auth_status: not_logged_in`. Die CLI-Inventur beweist keine dynamische Toolverfügbarkeit oder Repositoryberechtigung; diese wurde inzwischen über direkte MCP-Aufrufe geprüft.
Die Overrides registrieren nichts persistent, starten keine zweite Codex-Agentensitzung und umgehen keine abgelehnte Registrierung/Anmeldung.

- Context7: ausschließlich `resolve-library-id`, `query-docs`; Default Approval `prompt`; ohne API-Key.
- Playwright: `browser_navigate`, `browser_snapshot`, `browser_take_screenshot`, `browser_console_messages`, `browser_network_requests`, `browser_click`, `browser_fill_form`, `browser_select_option`, `browser_press_key`, `browser_wait_for`, `browser_close`, `browser_resize`; Default Approval `prompt`.
- Playwright: `--isolated --headless --browser msedge --block-service-workers --no-webmcp --codegen none`; kein persönliches Profil, kein CDP, keine gespeicherten Logins, kein brokerseitiger Zugriff. Unsafe Code, Evaluate, Uploads, direkte Netzwerkrequests und Extension-Zugriff fehlen in der Allowlist. Normale E2E-Tests bleiben spätere CLI/CI-Tests.
- GitHub lesend: `get_me`, `get_file_contents`, `list_branches`, `list_commits`, `list_issues`, `search_issues`, `issue_read`, `get_label`, `list_label`, `list_pull_requests`, `pull_request_read`, `actions_list`, `actions_get`.
- GitHub schreibend: nur `issue_write`, `label_write`. Default `writes`, beide Schreibtools zusätzlich `prompt`. Keine Repository-, PR-, Workflow-, Kommentar- oder sonstigen Schreibtools.
- Die Toolnamen allein begrenzen keine Owner-/Repo-Parameter. Deshalb ist eine technische Beschränkung auf das Zielrepository erst nach Authentifizierung überprüfbar. Falls OAuth keine ausreichende Repositorygrenze ermöglicht, ist ein Fine-grained PAT nur für `fjure/agentic-trading-platform` nötig: Issues read/write, Contents read, Pull Requests read, Actions read, Metadata read; sichere Umgebungs-/Secret-Injektion, kein Token im Chat oder Klartextdateien. Kein blindes Verbreitern auf classic repo/workflow-Scope.
- `issue_write` kann auch PRs ändern, `label_write` kann löschen: diese Methoden sind durch Auftrag und AGENTS.md verboten, trotz gemeinsamer Toolnamen. Jede Mutation bleibt approval-pflichtig.

Ungefährliche lokale Smoke-Tests (ausgeführt, beide Exit 0):

```powershell
node scripts/smoke-mcp.mjs context7
node scripts/smoke-mcp.mjs playwright
```

Die Smoke-Scripts nutzen das offizielle MCP-stdio-Protokoll; kein zusätzliches SDK, kein Serverwrapper, keine Anwendung.
Die Start-/Toolinventur zeigt alle vom Anbieter angebotenen Tools, die Codex-Konfiguration beschränkt anschließend die tatsächlich freigegebene Teilmenge.

## Blocker und kleinster nächster Schritt

Die automatische Approval-Prüfung hat die benutzerweite Registrierung der drei eingeschränkten MCPs **zusammen mit dem Start von GitHub OAuth** abgelehnt.
Begründung: globale dauerhafte Änderungen und anmeldungsbezogene Seiteneffekte einschließlich schreibfähiger Tools benötigen ausdrückliche Autorisierung.
Die abgelehnte Operation wurde nicht ausgeführt und nicht indirekt umgangen.

Die globale Registrierung wird nach Nutzerentscheidung verworfen; der abgelehnte Schritt wird nicht erneut versucht. Offen bleibt nur GitHub-Authentifizierung und ihr verifizierter Repository-Scope. Die frühere Aussage, dass eine globale Registrierung nötig sei, ist korrigiert.
Keine Freigabe für beliebige GitHub-Mutationen erforderlich; der ursprüngliche Prompt autorisiert nur den nachfolgenden definierten Issue-Sync.
### Konkreter projektbezogener Anmeldeweg, vom Nutzer auszuführen

Die offizielle GitHub-Codex-Anleitung dokumentiert für den Hosted Server einen PAT über `bearer_token_env_var`. Diese secretfreie Referenz ist inzwischen vorbereitet. Für die verlangte Repositorygrenze ist ein Fine-grained PAT vorzuziehen; keine breite OAuth-Freigabe lediglich zur Beseitigung des Blockers erteilen.

1. Im eigenen GitHub-Account einen Fine-grained PAT mit Ablaufdatum erstellen: Resource Owner `fjure`, **Only select repositories**, ausschließlich `agentic-trading-platform`.
2. Repositoryrechte: **Issues read/write**, **Contents read**, **Pull requests read**, **Actions read**, **Metadata read**. Issues write deckt auch Label-Erstellung/-Aktualisierung ab. Keine Contents-, PR-, Actions-, Workflow- oder Administration-Schreibrechte; keine zusätzlichen Account-/Organization-Rechte.
3. Bereits erledigt: die folgende secretfreie Referenz ist im bestehenden `[mcp_servers.github]`-Block ergänzt; alle anderen Server, Allowlisten und Approval-Regeln erhalten:

```toml
bearer_token_env_var = "GITHUB_PAT_TOKEN"
```

4. Den PAT außerhalb von Chat/Repository/Logs über eine sichere lokale Secret-Injektion als `GITHUB_PAT_TOKEN` in die Prozessumgebung der vollständig neu gestarteten Codex-App bereitstellen. Kein `setx`, keine Klartext-Token-Kommandos oder `.env`-Datei im Repository. Der Agent liest oder extrahiert den Token nicht. Ein PAT benötigt kein `codex mcp login`.
5. Im selben Projekt fortsetzen: GitHub-Tools im tatsächlichen Sitzungskatalog prüfen, ungefährliche MCP-Leseaufrufe für das Zielrepository ausführen und Scope/Issue-Berechtigung nachweisen. Ein leerer Branch-/Commitbestand darf nicht mit fehlender Berechtigung verwechselt werden. Erst danach den OpenSpec-Change anlegen und später die autorisierten Issue-Mutationen mit Read-back durchführen. Ein PAT-Scope-Nachweis erfolgt anhand der vom Nutzer bestätigten Einstellungen plus MCP-Evidenz, niemals anhand ausgelesener Credentials; breite oder ungeklärte Rechte bleiben blockierend.

Falls ausdrücklich OAuth gewählt wird, lautet das offizielle Login-Kommando mit ausschließlich prozesslokalem Projektserver-Override und OS-Keyring-Speicherung:

```powershell
Set-Location 'C:\Users\Domenik\ai-projects\agentic-trading-platform'
codex -c 'mcp_servers.github.url="https://api.githubcopilot.com/mcp/"' -c 'mcp_oauth_credentials_store="keyring"' mcp login github
```

Nach ausdrücklicher Nutzerfreigabe ausgeführt: Exit 1, `Dynamic client registration not supported`. Kein Browser-Freigabeschritt und kein erfolgreicher Login. Ohne neue nachgewiesene kompatible Clientregistrierung nicht wiederholen. Bei fehlender Keyring-Unterstützung ebenfalls abbrechen, keine Klartextspeicherung als Ersatz. Die verfügbaren OAuth-Scopes enthalten breite Rechte wie `repo`; eine Begrenzung auf ausschließlich das Zielrepository ist dadurch nicht nachgewiesen. Der Override registriert keinen globalen Server. Die zuvor abgelehnte Kombination aus globaler Registrierung und OAuth wurde nicht erneut ausgeführt oder umgangen; keine zweite Agentensitzung wurde rekursiv gestartet.

Danach Reihenfolge: Repository-MCP-Read-back/Berechtigungen → OpenSpec-Change → aktuelle offizielle Produktquellen/Versionen → proposal/specs/design/tasks/ADRs/Contracts/Traceability → statische und Generator-Prüfungen → OpenSpec strict validation → alle Leaf-Task-Issues per MCP → Read-back und Mapping. Weiterhin kein Anwendungscode.
Der generierte Angular-Client ist wegen des ausdrücklich dokumentationsbeschränkten Auftrags als temporäres Generator-Prüfartefakt zu behandeln; keine Anwendung implementieren.

Issue-Sync-Mapping: `docs/development/github-issue-sync.json` enthält den ehrlichen Blockerstatus und eine leere Taskliste.
**0 synchronisierte Issues**, kein Read-back, keine erfundenen Issue-Nummern/Hashes/Zeitpunkte, kein Milestone/Label angelegt.
Geplantes Change-Label: `change:paper-trading-vertical-slice`; zusätzlich `openspec`.

## Update, Disable und Uninstall

- Update: offizielle Registry/Quellen erneut prüfen, dann `npm install --prefix .tooling --save-exact <package>@<geprüfte-stabile-version> --offline=false`; Lockfile und diese Dokumentation gemeinsam aktualisieren. OpenSpec anschließend `./scripts/openspec.ps1 update`.
- Disable: im tatsächlich geladenen Scope `[mcp_servers.<name>] enabled = false` setzen, Sitzung neu laden und `codex mcp list` verifizieren. Nicht automatisch globale fremde Server ändern.
- Projektbezogene Entfernung: den jeweiligen `[mcp_servers.<name>]`-Block samt zugehörigen Tool-Overrides aus `.codex/config.toml` entfernen und neu laden. `codex mcp remove` verwaltet hier benutzerweite Einträge und wird für diese ausschließlich lokalen Server nicht benutzt. OAuth-Abmeldung benötigt gegebenenfalls ebenso den projektbezogenen CLI-Override.
- Lokale Paket-Deinstallation: `npm uninstall --prefix .tooling @upstash/context7-mcp @playwright/mcp @fission-ai/openspec`; vorher entsprechende Konfiguration deaktivieren/entfernen und dokumentierte Skills auf weiter benötigte Dateien prüfen.
- Grafana bewusst nicht installiert: erst reale Instanz plus eigener read-only Service Account.
- Nicht installiert: Alpaca, IBKR, CCXT, Broker, Order, Jev, PostgreSQL, Filesystem, Shell, eigenständiger Git, Docker, Memory, generischer Reasoning und OpenAPI MCP.
- Keine Secrets im Repository oder dieser Dokumentation gespeichert; keine Broker-/Jev-Credentials gelesen, keine Orders oder Kontozugriffe durchgeführt.

## Offizielle Quellen

Abruf aller folgenden Quellen: 2026-10-12. Abfragen auf offiziellen Repositoryseiten können zwischen Releases liegen; tatsächlich installierte Paketversionen stammen aus der offiziellen npm-Registry.

- [OpenAI MCP-Konfiguration, Allow-/Denylists, Approval und OAuth](https://developers.openai.com/codex/mcp/)
- [OpenAI Konfigurationsreferenz](https://developers.openai.com/codex/config-reference/)
- [OpenSpec offizielles Repository: Codex, Profile, Telemetrie](https://github.com/Fission-AI/OpenSpec)
- [OpenSpec CLI](https://github.com/Fission-AI/OpenSpec/blob/main/docs/cli.md)
- [Upstash Context7 offizielles Repository](https://github.com/upstash/context7)
- [Microsoft Playwright MCP offizielles Repository](https://github.com/microsoft/playwright-mcp)
- [Offizieller GitHub MCP](https://github.com/github/github-mcp-server)
- [GitHubs Codex-Installationsanleitung](https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-codex.md)
- [GitHub Hosted Server und Toolsets](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- [GitHub Fine-grained PAT erstellen und Repositoryauswahl begrenzen](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [GitHub Fine-grained PAT: erforderliche Rechte, einschließlich Issues und Labels](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- [Öffentliche OAuth-Resource-Metadaten des Hosted GitHub MCP](https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp/)
- [Offizieller MCP-stdio-Transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
