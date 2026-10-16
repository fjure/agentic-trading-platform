# PTV-20: Angular-Material-Supervisor

Prüfstand 2026-10-16; REQ-CTL-001/004/005/006/007, D-08, ADR0003. Angular/Material/Forms22.2.2, NgRx Signals22.0.1 und Playwright1.64.1 sind exakt gepinnt. Node22.23.3 bleibt die verifizierte Buildtoolchain. NgRx-Peerkompatibilität im offiziellen npm-Register geprüft; aktuelle Angular-/Material-/SignalStore-Dokumentation über Context7 gelesen. Es gab keine echten Broker-/Jev-Requests oder Orders.

## Verhalten

Materialübersicht und acht Historienansichten zeigen Modus, Kontoalias, getrennte Dienstverbindungen, exakte Portfolio-Dezimalstrings, Workerheartbeats, Marktevidenz, typisierte Entscheidungen mit Confidence/Verteilungen und Rubrik, Vorschläge, Risk-Reasoncodes, Teilfüllungen, Reconciliation, gefilterte Audit-Timeline und verfügbare Kosten. Unbekannte Kosten bleiben ausdrücklich unbekannt. Trace-Verknüpfungen zeigen passende geladene Markt-/Entscheidungs-/Vorschlags-/Risiko-/Orderdaten. Noch nicht vollständig durchsuchte Historien bleiben sichtbar markiert. Eine ausdrückliche Aktion durchsucht jeweils eine weitere begrenzte Seite je Ressource; der Browserfall findet so die zweite Marktseite im gefilterten Trace.

Rekursive öffentliche DTO-Projektionen entfernen unbekannte verschachtelte Felder vor dem Store und weisen ungültige Werte ohne Rohdaten zurück. Zwei reine Projektionsprüfungen belegen exakte Dezimalstrings/null, entfernte Canaries und Schutz vor Prototype-Pollution.

Der SignalStore verwaltet nur gemeinsamen Anzeigezustand und Filter im Speicher. REST verwendet den unveränderten generierten Client mit Same-Origin-Basis. Formulare/Dialoge bleiben lokal; der Aktivierungscode wird beim Submit sofort entfernt. Keine DTO-, Cursor- oder Codepersistenz in localStorage, sessionStorage oder IndexedDB. Ein Reload liest den Backendzustand neu. Sitzungswiderruf verwirft die Anzeigedaten.

Ein separater begrenzter SSE-Fetch-Reader erkennt Kommentarheartbeats, dedupliziert Eventkennungen und überwacht Aggregatversionen. Reconnect verwendet Last-Event-ID; 410, reset-required und Versionslücken veranlassen einen neuen Snapshot. Normale Datenaktualisierungen verschieben den Streamcursor nicht. Nach 45 Sekunden ohne Signal erscheint stale; die Anzeige behauptet keinen aktuellen Zustand bei Verbindungsabbruch.

Pause, Fortsetzen und Kill verlangen eine ausdrückliche Bestätigung. Der Server entscheidet verbindlich. Ablehnungen bleiben sichtbar, ohne optimistischen Erfolg. Nach unklarer Netzwerkantwort verwendet die ausdrücklich gewünschte Wiederholung denselben Schlüssel. Pause/Kill bleiben auch bei einer ungeklärten Fortsetzungsanfrage verfügbar; deren überholte Antwort kann die neuere Kontrollantwort nicht überschreiben. Kein LIVE-Modus, Order-submit oder Client-Risikoentscheid wurde ergänzt.

## Prüfevidenz

- `scripts/test-dashboard.ps1`: Produktionsbuild erfolgreich; abschließender gemeinsamer Lauf mit rekursiver DTO-Projektion und Trace-Pagination: **18 passed in22.1s** (16 tatsächliche Browserfälle und zwei reine Projektionsprüfungen).
- 399 vorhandene Contract-/Security-/Unit-/Property-/Offline-Replay-/Adapterprüfungen passed in6.59s; Ruff bestanden. Keine Backend- oder Migrationsänderung, deshalb keine neue PostgreSQL-Zertifizierung behauptet.
- Zehn tatsächlich ausgelieferte synthetische Browserantworten gegen kanonische OpenAPI-Schemata validiert. Sie sind UI-Fixtures, keine behauptete integrierte Paper-Ausführung.
- OAS3.1.2/18 Operationen, zehn interne Schemas und Beispiele valide; oasdiff gegen Basismain87ea2cf meldet keine Änderungen.
- OpenAPI Generator7.26.0 erzeugte zwei bytegleiche65-Dateien-Bäume; der vorhandene Anwendungsclient stimmt vollständig überein; TypeScript erfolgreich. Keine generierte Datei geändert.
- Playwright-MCP: isolierte lokale Aktivierungs-/Übersichtsinspektion und Accessibilitysnapshot, danach Screenshot ohne Aktivierungscode unter ignoriertem `.tooling/output/ptv-20-supervisor.png`; Desktopdarstellung visuell geprüft. Mobile390px-Navigation und Details im automatisierten Browser ohne horizontales Seitenoverflow geprüft.
- Produktionsbuild meldet weiterhin Warnungen: initial619.87kB gegenüber500kB-Warnbudget, Komponenten-CSS7.12kB gegenüber4kB-Warnbudget. Harte Grenzen1MB/8kB nicht überschritten; Budgets nicht gelockert. Keine Performancezertifizierung behauptet.

## Review

Drei begrenzte Subagenten mit getrennten Dateien implementierten Datenzustand, Oberfläche und Browsertests. Gegenseitige Sourceprüfungen bestätigten die Grenzen; der Hauptagent prüfte und integrierte. Tatsächliche Browsertests fanden einen nativen Formularreload durch ungebundenes ngSubmit; beide Formulare verwenden jetzt abgefangenen submit. Der Source-Review fand blockierte Notfallaktionen nach unklarem Ergebnis; dies wurde mit einem eigenen Browserfall korrigiert. Veraltete englische Testmeldungen und eine falsche erwartete Dezimaldarstellung wurden anhand der tatsächlichen vertraglichen Werte berichtigt.

| Grenze | Evidenz |
|---|---|
| Backend ist verbindlich | Generated-Client-Kommandos, keine Brokerimporte/Orders/Riskberechnung; Resumeablehnung und Kill-Latch im Browser geprüft |
| Idempotenz | Unklare Übertragung wird nur ausdrücklich mit demselben Key wiederholt; Notfallaktion bleibt möglich |
| Secret-/Datenisolation | Synthetischer Sessioncookie HttpOnly/Strict; lokale Eingabe entfernt, Browserpersistenz leer; unbekannte verschachtelte Antwortfelder vor Store entfernt, numerisches Geld und unsichere Mapkeys zurückgewiesen |
| Decimal/UTC | Exakte vertragliche Strings ohne Geldberechnung; schemavalidierte Fixtures und sichtbare Teilfüllmenge |
| Reconnect | Tatsächlicher lokaler HTTP-/SSE-Stream, Heartbeat/45s-Stale, Last-ID/410/Reset, Deduplizierung und Versionslücke geprüft |
| Audit/Trace | Expliziter Tracefilter und strukturierte korrelierte Evidenz; keine Gedankenketten oder rohen Anbieterantworten |

Die echte Prozessverdrahtung, Deployment-/Failure-Injection und Paperintegration bleiben PTV-21 bis PTV-23. Der synthetische Loopback-Testserver ist kein Betriebsserver. Die UI ersetzt den unabhängigen Kontrollpfad nicht. Keine echte Kontoberechtigung oder Anbieterqualität wurde geprüft.

Plan40REQ/23DAG und OpenSpec strict erfolgreich; abschließender Quellscan334Dateien/0Credentialmuster. Dokumente, Traceability und Issue20 werden im selben Durchlauf synchronisiert.
