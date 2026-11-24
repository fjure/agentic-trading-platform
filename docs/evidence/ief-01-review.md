# IEF-01 — begrenzter unabhängiger Abschlussreview

Nachprüfung am 2026-11-24 im Issue-Worktree auf Ausgangs-SHA `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Ergebnis: **keine offenen P1-/P2-Codebefunde im geprüften UI-/Hostingumfang**. Die unten benannten Befunde wurden am abschließenden handgeschriebenen Code nachgeprüft. Das ist keine vollständige Runtime-, Accessibility- oder menschliche UX-Freigabe. Die formative Übergabe bleibt **HUMAN_UX_VALIDATION_PENDING**.

## Umfang und Nachweise

Read-only geprüft: `apps/dashboard/src/app/app.routes.ts`, Shell, alle zehn Prototypscreens, relevante bestehende Companion-/Quantlinks, `tests/e2e/ief-prototype.spec.ts`, ergänzte Companionfälle, Hostingfixture und Frontend-Bridgefälle sowie der IEF-CI-Diff. Nur dieses Reviewdokument wurde im Abschlussdurchgang verändert; keine UI-, Contract-, Git-, Issue- oder Taskmutationen.

Die lokale Desktop- und Mobile-UNKNOWN-PNG wurden visuell betrachtet: Kontext und Prototypkennzeichnung sind sichtbar, die Textreihenfolge ist lesbar, UNKNOWN führt zum bestehenden Vorgang. Zwei Screenshots belegen keine vollständige Zugänglichkeit.

Laufresultate stammen vom Hauptagenten und werden hier ausdrücklich nicht als eigene neue Browserausführung ausgegeben: erster ausgewählter Browserlauf 40/42 bestanden, danach die zwei Accessibilitybefunde korrigiert; fokussierte Demo-/Accessibilitynachprüfung **4 passed**. Der vollständige ausgewählte Wiederholungslauf war bei dieser Nachprüfung noch aktiv; sein Erfolg wird hier nicht vorweggenommen. Maßgebliche abschließende Kommandos, Buildprovenienz und Endresultate gehören in `ief-01.md` und die Fachnachweise. Kein langer zusätzlicher Testlauf für diesen Review.

## Geschlossene Befunde

| Befund | Nachprüfung des endgültigen Codes |
| --- | --- |
| P2: bestehende Research-/Quant-/Paperlinks durch neuen Konfliktblocker abgewiesen | Der Adapter erhält unterstützte Provenienzkombinationen einschließlich Research plus Quant sowie Research plus Paperhash. Root-Deep-Links leiten nach `/operations` weiter und behalten Query und Fragment. Unveränderte Beispielbindungen: `researchReport=report-1&researchReportVersion=2`, `quantExperiment=experiment-1&quantBatch=batch-1&quantReport=report-1` und `researchIdea=idea-1#alpaca-paper`. |
| P2: Quantreport ohne erforderliche Bindung verborgen | `quantReport` ohne `quantBatch` wird ausdrücklich abgewiesen. Researchberichte verlangen ihre positive ursprüngliche Versionsbindung. Doppelte Kennungen, freie URLs und widersprüchliche Research-/Backtestauswahl ergeben den erklärten Fehlerzustand. |
| P2: kodierte API-Pfade als HTML-Erfolg in der Browserfixture | Der Fixture-Classifier dekodiert vor dem API-Fallbackschutz; kodierte reservierte APIvarianten werden 404, defekte Kodierungen 400. Fehlende Assets und mutierende UI-Methoden erhalten keinen erfolgreichen SPA-Fallback. Die synthetische Fixture behauptet keine vollständige Runtimeparität. |
| P2: feste Demo-Finanzwerte unter Paperlabel | Home/Portfolio/Insights akzeptieren nur die definierte Demoquelle und Demo-ID. Fremder Paper-/Demokontext zeigt Nichtverfügbarkeit statt anderer Finanzwerte. |
| P2: fremde Objektart als gültige Strategie angezeigt | Objektallowlisten sind nach Strategie-/Taskscreen getrennt; fremde Versionen sowie unbekannte oder mehrfach gebundene Strategieherkunft am Orderweg werden abgewiesen. Kein stiller Wechsel zum direkten Portfoliozweig. |
| P2: historischer Bericht ohne Zeitbereich | Die historische Evidenz nennt einen ausdrücklich synthetischen Zeitraum 2025, getrennt von letzter Bestätigung, und fehlende OOS-/Holdoutphasen. Keine erfundenen Performancemetriken. |
| P2: eingegebene Orderterms durch feste Vorschau ersetzt | Die bewusst begrenzte Fixture akzeptiert ausschließlich SPY/BUY/Menge 1/LIMIT 100.00 USD. Andere Eingaben werden klar blockiert und nicht ersetzt. Exakte Werte bleiben Strings; keine clientseitige Riskentscheidung. |
| P2: Strategieherkunft beim Vorschau-/Risk-/Bestätigungsschritt verloren | Die Schritte erhalten Queryprovenienz. Der direkte Zweig bezeichnet sich ausdrücklich als menschlicher Entwurf ohne Strategiefreigabe; fremde Strategie-ID wird abgewiesen. |
| Zusätzliche lokale Zustandsbefunde | Änderung oder Verwerfen der Originalfrage entzieht Interpretations- und Ersatzhypothesenbestätigung. Die Bestätigung erklärt vor dem Button die spätere Übermittlung an die Paper-Infrastruktur, fehlenden Einsatz echten Kapitals und die aktuelle Wirkungslosigkeit des Prototyps. Annahme wird ausdrücklich von Fill unterschieden. |

Die Legacyprüfung beschränkt sich inzwischen nicht auf unauthentifizierten URLerhalt: ergänzte bestehende Companionfälle folgen tatsächlich erzeugten Report-/Quant-/Experimentlinks mit ursprünglicher Researchprovenienz und Reload. Dabei bleiben Reportversion, Experiment-/Batchbindung und die Anzahl bereits expliziter Entscheidungen erhalten. Diese kontrollierten Browserfixtures sind UI-Regressionsbelege und ersetzen keine spätere echte DB-/API-/Runtime-Journey.

## Sicherer Flow und Accessibilitygrenzen

Alle zehn Screenfälle und die verbundene S5/S6/Evidenz/Eignung/S9/Vorschau/Risk/Bestätigung/Status/S10-Kette sind im Code vorhanden. Sie tragen dauerhaft die Kennzeichnung klickbarer synthetischer Prototyp. Neue Experiencewege haben keinen HTTP-Client oder Brokerport. Die Tests erwarten dort null APIrequests. Quelle, Revisionen und letzte Beispielbestätigung sind sichtbar; Reload erklärt ausdrücklich feste Fixture statt Persistenz.

Stale und fehlende Voraussetzungen sperren die neue Bestätigung. UNKNOWN nennt dieselbe Operation `prototype-operation-01` und ihre Idempotenzbindung, bietet Lesen statt Ersatzorder und gibt weder Budget noch Fill als bestätigt aus. Vorhandene unabhängige Paper-/Pause-/Killkontrollen bleiben erreichbar. Freie Researchfrage, Interpretation, optionale fremde Verarbeitung und getrennte Ersatzhypothese sind unterscheidbar; Providerverarbeitung bleibt ohne konkrete Bindung gesperrt.

Shell und Prototyp enthalten Sprunglink, fokussierbare Titel, Material-Sidenav, Escape/Fokusrückgabe, Statusansagen und Text zusätzlich zu Farbe. Der Hauptagent hat die beiden zuerst fehlgeschlagenen Accessibilityfälle einschließlich 200-Prozent-Überlauf korrigiert und fokussiert nachgeprüft. Eine manuelle Screenreaderprüfung und die vollständige fachfremde Verständniskontrolle sind dadurch nicht ersetzt. Für den Umfang der konkreten technischen Kontrast-/Zoom-/Viewportnachweise gelten die finalen Browserresultate, nicht eine pauschale WCAG-Zertifizierung.

Die Bridgefälle belegen lesendes SPA-Hosting und API-/Asset-/Methodengrenzen; sie führen keinen Angular-Router aus. Insbesondere eine erfolgreiche HTML-Auslieferung für `/operations/paper` bedeutet keine vorhandene Fachroute. Der tatsächliche vorhandene Paperzugang bleibt `/operations#alpaca-paper`.

## CI und verbleibende Abnahme

Der geprüfte CI-Diff ergänzt beide Issuebranchfilter um `codex/ptv-*-ief-*`, erhält die echte Remote-main-Mergebasis für Breaking-Diffs und nimmt Frontend-Bridge-/Projektionstests auf, ohne bisherige Pflichtfälle zu entfernen. Dies belegt keine späteren vollständigen J1–J6-Manifeste, nächtlichen Securityläufe oder den erforderlichen Repository-Ruleset-Read-back. Diese Nachweise und neue fachliche Runtimepfade bleiben den abhängigen Paketen beziehungsweise IEF-09 zugeordnet.

**HUMAN_UX_VALIDATION_PENDING** bleibt ein echter Freigabeblocker für die formative Übergabe, kein Codebefund und kein durch Browserautomation erfülltes Kriterium. Es liegen keine hier durchgeführten menschlichen Sitzungen, Bewertungen oder Freigaben vor. Das getrennte `ief-human-ux-protocol.md` enthält die verlangte Stichprobe und leere Vorlagen. IEF-01 braucht mindestens fünf fachfremde Personen, darunter zwei Mobile- und zwei Desktopsitzungen, alle sechs Wege und hundert Prozent sichere Entscheidungen in den simulierten Sicherheitsfällen. IEF-02- und IEF-09-Nutzernachweise bleiben getrennt. Keine menschlichen Ergebnisse wurden simuliert oder erfunden.

**Aktueller Abnahmezeitpunkt:** Nach dem späteren Nutzerauftrag werden alle drei menschlichen Prüfungen in IEF-09 durchgeführt. Frühere Aussagen in diesem Nachweis über eine menschliche Freigabe als technisches IEF-01- oder Übergabegate sind historisch. Der Prüfstatus bleibt HUMAN_UX_VALIDATION_PENDING.

Ein unabhängiger Nachreview der Rollen-Fixtures und des neuen Abnahmezeitpunkts ergab keine offenen P1/P2-Befunde nach Korrektur einer missverständlichen Zuordnung der Schwellen in ux-validation.md. Die Schwellen gelten für die drei Runden in IEF-09.
