# Planungs- und Reviewnachweis

Stand 27.10.2026. Ausschließlich Propose. Keine neue Anwendung, keine Vertragsänderung im kanonischen OpenAPI, keine neue Clientgenerierung oder Runtimeaktivierung. Vorhandene fremde Änderungen in README.md/angular.json bleiben außerhalb dieses Changes.

## Ausgeführte Prüfung

- Lokaler HEAD und remote main waren `5bffd2488ad3b42b3a2284c75d9a6ff4db16437d`; GH CLI 2.100.0 bestätigte Ziel und Login fjure. Alle bisherigen Issues #1..#68 geschlossen. Repositoryconnector lesend funktionsfähig.
- Vision, Architekturprinzipien, Roadmap, OpenSpeckontext, vorhandene Main-Specs und relevante abgeschlossene Changes sowie konkrete Implementierung/Evidenz gelesen; Konsolidierungsbedarf in discovery.md dokumentiert.
- Context7 zuerst versucht, Monatsquote ausgeschöpft; öffentliche offizielle Quellen in provider-assessment.md geprüft. Keine persönlichen Rechte bestätigt.
- `./scripts/openspec.ps1 validate retrospective-research-backtesting --strict`: erfolgreich, auch nach Reviewänderungen erneut; `status` meldet 4/4 Planungsartefakte.
- Konkrete Issuevorschau aus tasks.md: acht eindeutige Task-IDs, topologisch geordneter DAG und Pflichtfelder Requirements/Paths/Acceptance/Tests/DoD geprüft. Suchlauf über offene/geschlossene Issues und MCPsuche RRB ohne Treffer; keine Duplikate.
- MCP-Issueanlage versuchte exakt RRB-01 und scheiterte mit 403 `Resource not accessible by integration`; kein Issue über MCP angelegt. Autorisierter GH-CLI-Fallback wird als eigener Transport nachgewiesen. Kein Token-/Credentialzugriff.

## Unabhängiger quantitativer Review

Separater read-only Subagent prüfte Vorschlag/Design/Spezifikationen/Tasks gegen Dataset, Simulator, Evaluation, Kalender, Coverage, Metriken und Hashimplementierung. Ergebnis: keine kritischen oder wesentlichen quantitativen Planungsblocker. Die bewusst explorative UNKNOWN-Maßnahmenpolicy ist kein Maßnahmenfreiheitsbeleg.

Präzisierungen übernommen: konkrete `statement.actions` statt verlustbehaftetem no_actions-Bool verwenden; bestehenden Lizenzstatus BLOCKED auf DENIED abbilden; heutige authentische Kalenderinhalte von fehlendem historischem Zeitvertrauen trennen. Zuordnung RRB-03/05/06 und T-RRB-015..020.

## Unabhängiger Architektur-/Safetyreview

Separater read-only Subagent prüfte APIkompatibilität, Runtime-/SQLisolation, Sicherheitsgrenzen, Testpflichten und Taskgraph anhand des trading-safety-review-Skills und tatsächlicher Quellen. Drei Befunde vollständig behoben und erneut geprüft:

1. Alter Worker könnte sonst vor Versionsprüfung neue Runs leasen: D6 fordert DBkompatibilitätstrigger vor Claim/Publish und Negativtest mit unverändertem alten SQL, zusätzlich Migrations-/Rollbackdrain.
2. Lizenzstatus ist heute mutable und dem Worker nicht direkt lesbar: D5a definiert enges nicht sensibles Read-only-View, immutable Receipt, Statusmapping und fail-closed aktuellen Check; keine breiten Researchrechte.
3. Discoveryreferenz fehlte bei frühem Review: discovery.md ergänzt mit genauer Basis und Konsolidierungslücke.

Zusätzlich gemeinsame DBworkerbarriere und oldest-job-Admission auf Assessmentjobs erweitert. Re-review: keine verbleibenden kritischen Planungsblocker, DAG azyklisch und Zuständigkeiten abgegrenzt.

## Safetyklassifikation

| Invariante | Plan-/Quellprüfung | Implementierungsnachweis dieses Changes |
|---|---|---|
| Agenten ohne Broker-/Orderport | REQ-RRB-007, D1/D6 | offen: T-RRB-012 |
| deterministische Risk-/Executiongates | unverändert, Bestandsregression Pflicht | offen: RRB-08 |
| LIVE unerreichbar / Dispatch aus / sicherer Restart | REQ-RRB-007, D6, Migration Plan | offen: T-RRB-009/012 |
| Decimal / UTC | REQ-RRB-004, D2/D3 | offen: T-RRB-007/008 |
| Pause/Kill/Reconciliation unabhängig | gemeinsame Budgets, DBbarriere | offen: T-RRB-012 unter Last |
| Execution-only Alpaca / Worker-only Jev außerhalb Backtest | keine neuen privaten Mounts oder Netzports | offen: ACL-/Canary-/Aufrufzählertests |
| unveränderliche Referenzen und Audit | D2/D5a/D6 | offen: PG-/Fence-/Publishprüfung |
| Reservierungen, Ablauf, unknown orders, Partialfills und Cancelrennen | kein geänderter Handelspfad | bestehende Regression bei RRB-08 erneut erforderlich |

Planreview zertifiziert keine neue Runtime. Broker-/Jev-/Providercalls, echte Datensätze und Providercredentials wurden nicht verwendet. Die geplante PostgreSQL-/HTTP-/Browser-/Lasttestkette wird erst während Apply ausgeführt. Source-/Contract-/Clientchecks der neuen Implementierung werden nicht als jetzt bestanden dargestellt.

## Veröffentlichung und Issueevidenz

Verbindlicher maschinenlesbarer Read-back: `docs/development/github-retrospective-issue-sync.json`; graphische/textuelle Zuordnung in issue-graph.md. Veröffentlichung wird erst nach erfolgreichem Push und SHAvergleich bestätigt; diese Datei behauptet keinen vorweggenommenen Push. Alle acht Implementierungscheckboxen bleiben offen. Main-Spec-Sync und Archive sind nicht Teil dieser Freigabe.

Ausgeführt: acht Issues #69..#76 angelegt, acht direkte Read-backs und repositoryweiter Eindeutigkeitsabgleich bestanden; 15 native Abhängigkeiten angelegt, acht Vorwärts- und acht Rückwärtsreadbacks bestätigen exakt denselben DAG. Keine fremden Inhalte, Labels oder Kommentare überschrieben, keine Issues geschlossen. Statische Zusatzprüfung bestätigt 12 eindeutige Requirements, 20 eindeutige Szenarien, acht offene Tasks mit Pflichtmetadaten, vollständige Requirementzuordnung und topologische Ordnung.

`scripts/scan-source-secrets.py`: 1135 versionierbare Quelldateien, null Credentialmusterbefunde. `git diff --cached --check`: erfolgreich; Staging umfasst ausschließlich die zwölf neuen Plan-/Mappingdateien, keine bestehenden Anwendungspfade. Der Scan liest keine privaten Credentialstores.

## Apply-Empfehlung

Die fachliche Planung ist nach unabhängigen Reviews bereit für einen gesonderten OpenSpec-Apply-Auftrag, beginnend mit RRB-01/#69, sobald Veröffentlichung und letzter Read-back erfolgreich bestätigt sind. Unbekannte persönliche Rechte und historische Datenqualität bleiben ausdrücklich unverifizierte externe Grenzen. Bekannte nicht modellierte Maßnahmen oder verweigerte Rechte werden nicht durch diese Empfehlung überstimmt. Vor späterer Archivierung ist die dokumentierte Konsolidierung der vorausgehenden Specs gesondert freizugeben.
