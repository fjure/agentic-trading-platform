# Definition of Ready

## Planungsbereitschaft und Ausführungsbereitschaft

Diese Unterscheidung ist verbindlich: vorhandene Artefakte und gültige OpenSpec-Dateien erteilen keinen Applyauftrag und keine produktive Researchfreigabe.

| Kriterium | Planungsnachweis / Gate |
| --- | --- |
| Tatsächlicher veröffentlichter Stand | discovery.md; main 0442f29571e53b983e9e08f2180c4bd3536e0b6a, Issue-/PR-/CI-Readback |
| Istfähigkeiten und echte Lücken | discovery.md, drei Domänenmodelle; keine Vision als Implementierung |
| Zielgruppen, sechs Journeys, Navigation | personas.md, user-journeys.md, navigation-design.md |
| Zehn Screens/Einsteiger/Experten/Designsystem | ux-architecture.md, Progressive Disclosure und Zustandsverträge |
| Portfoliowahrheit und Datenqualität | portfolio-read-model.md; null+Reason, Decimal/UTC, kein zweiter Ledger |
| Strategien/Resultate/Fähigkeit | strategy-experience.md und IEF-S01-S03 |
| Aufgaben auf bestehender Runtime | agent-task-model.md, design.md D3/D4; Analysequelle vs Run klar |
| Sicheres Paper | guided-order-design.md; bestehende Human/Risk/Executiongrenzen |
| Sechs getrennte Entscheidungen | security-and-permissions.md; neuer Erklärungszweck ausdrücklich spezifiziert |
| Normative Szenarien und Coverage | sechs Capability-Deltas, traceability.md, test-strategy.md |
| Getrennte Arbeitspakete | neun offene Work Packages und tasks.md |
| GitHub-Sync | github-issues.json muss synchronisiert, Hash-/Label-/State-Readback und bidirektionale native Kanten enthalten |
| Strict Validation | planning-verification.md muss tatsächlichen erfolgreichen Aufruf belegen |
| Unabhängige Reviews | reviews/; keine offenen kritischen Planungsbefunde vor Empfehlung |
| Veröffentlichter geprüfter Plan | planning-verification.md mit Branch-/main-SHA und Remoteprüfungen |

## Noch offene Ausführungsgates

- Neuer ausdrücklicher Nutzerauftrag für Apply nach Vorlage dieser Planung ist zwingend.
- ARP-10/#105 ist technisch abgeschlossen und beide aktuellen CI-Gates sind grün. Die Voraussetzung für IEF-04/06/09 ist anhand der veröffentlichten Nachweise erfüllt; native Provenienzkanten bleiben bestehen. Reale Zweck-/Provider-/Modellfreigaben bleiben separat.
- Die historische Contracts-Bootstrapblockade ist auf0442f29 behoben. IEF muss eigene Contracts-/Researchchecks am tatsächlichen Implementierungs-SHA weiterhin grün belegen.
- Reale Providerrechte, Verarbeitung, Budgets, modellgebundene Fachfreigabe und private Betreiberprüfung bleiben separat. Keine Freigabe für Entwickler, echte Modellaufrufe oder Orders auszuführen.
- Öffentliche Nutzung, echte Mehrbenutzerisolation, regulatorische Einordnung/Datenschutz und personalisierte Empfehlungen verlangen gesonderte fachliche/rechtliche Entscheidungen; lokale Präferenzen sind keine Geeignetheitsprüfung.

Empfehlung erst nach Planungschecks: gesonderter Applyauftrag für die geplanten Pakete möglich; keine pauschale Betriebsfreigabe. Der finale tatsächliche Prüfstand steht in planning-verification.md.


## Abschließender Planungsstand

Alle Planungsnachweise einschließlich Reviews, strengem OpenSpec, vollständigem Issue-/Hash-/Native-Readback und erster Git-Veröffentlichung sind erbracht; siehe planning-verification.md und git-publication.json.0/9Pakete bleiben implementiert. Damit ist die Planung zur Nutzerprüfung bereit, Apply weiterhin nicht beauftragt. Auf der ersten reinen Planungsveröffentlichung gestartete neue CI-Läufe wurden zunächst laufend beobachtet und werden nicht als abgeschlossen ausgegeben.
