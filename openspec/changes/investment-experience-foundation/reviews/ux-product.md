# Unabhängige UX- und Product-Reviews

## Prüfrahmen

Read-only-Review der Rootartefakte `design.md`, aller sechs `specs/*/spec.md`, `work-packages/IEF-01.md` bis `IEF-09.md` sowie `test-strategy.md`; zusätzlich Abgleich mit den Screen-/Journeyverträgen. Der Reviewer hat die fünf UX-Begleitdateien erstellt, aber weder Rootdesign, Capability-Deltas noch Arbeitspakete verfasst. Die folgenden Reviews beurteilen eigenständig diese fremdverfassten Rootartefakte; die eigenen UX-Dateien sind ausdrücklich nicht alleinige Evidenz. Stand: Planungsreview am 2026-11-23, keine Browsernutzung oder Implementierungsabnahme.

## UX Review

**Ergebnis: keine P1; zwei konkrete P2-Konsistenzbefunde, vor Applyplanung zu beheben.**

### UX-P2-01 – Der Erstnutzerweg hat zwei unterschiedliche definierte Endpunkte

**Belege:** `specs/investment-onboarding/spec.md`, IEF-T-O01, verlangt gekennzeichnetes Demo-Cockpit und lokale deterministische Analyse. `design.md` D3 und `work-packages/IEF-04.md` konkretisieren eine persistierte snapshotgebundene Portfolioanalyse, die über PORTFOLIO_ANALYSIS ohne Run erscheint. `user-journeys.md` J1 und letzter Erstnutzernachweis beenden denselben Weg dagegen mit einer lokalen Forschungsfrage und bloßer Testbarkeitsbeurteilung ohne Quellen. Die Screenverträge S3/S4 beschreiben noch primär Paper und deklarieren eine Portfolioanalystenrolle unverfügbar, ohne den neuen separaten lokalen Analyseauftrag klar zu zeigen.

**Folge:** Eine Implementierung könnte J1 mit einem „Daten fehlen“-Dialog abhaken, obwohl normativer Onboardingtest eine echte Analyse des explizit gewählten Demosnapshots verlangt. Das wäre sicher, würde aber das konkret geplante erste Produkterlebnis verfehlen.

**Konkrete Korrektur:** J1, S1–S4 und Nachweistext auf denselben Weg ausrichten: ausdrückliche Demoauswahl mit demoRunId → bestätigter synthetischer Snapshot → ausdrückliches POST portfolio-analyses → persistierter deterministischer Qualitäts-/Bestandsbefund → lesbares Ergebnis mit analysisId und nachvollziehbarer Wiederaufnahme. Der lokale Ideendialog bleibt zusätzlicher Weg J4; er ersetzt J1 nicht. Rolle/Run und lokale Analyse sind getrennt zu erklären. Keine automatische Demorun-/Analyseanlage durch GET oder Navigation.

### UX-P2-02 – Das Onboardingpaket beansprucht den vollständigen J1 vor seinen erforderlichen Analyse-/Cockpitpaketen

**Belege:** `work-packages/IEF-02.md` hängt nur von IEF-01 ab, beansprucht eigenständig überprüfbaren vertikalen Schritt und nennt J1 im positiven Abnahmetext. Der normative IEF-T-O01 endet aber mit lokaler Analyse. Deren Persistenz entsteht erst in IEF-04, die Auffindbarkeit im Task Center erst IEF-06 und das verbundene Cockpit erst IEF-08. `work-packages/IEF-09.md` und `test-strategy.md` führen den integrierten J1 später vollständig aus.

**Folge:** Es bleibt unklar, welche J1-Evidenz bei IEF-02 tatsächlich ausreicht. Ein Paket könnte zu früh mit einem Platzhalter als vollständige Journey abgeschlossen werden oder die Implementierung bekäme unnötig zyklische Abhängigkeiten.

**Konkrete Korrektur:** IEF-02 ausdrücklich als Präferenz-/Onboardingteilstrecke abnehmen; J1-Ende-zu-Ende und IEF-T-O01 bleiben bis IEF-04/06/08/09 offen. Bei IEF-02 keine Analysefähigkeiten oder Resultate behaupten. Keine zusätzliche zyklische Paketkante einführen; das Integrationsgate trägt den vollständigen Journeybeleg.

### Geprüfte positive UX-Eigenschaften

Alle zehn Pflichtscreens sind einzeln spezifiziert: Guided Onboarding, Personal Dashboard, Portfolio Hub, Portfolio Insights, Strategy Discovery, Strategy Detail, AI Task Center, AI Task Detail, Guided Paper Order und Activity Timeline. Pro Screen sind Nutzerziel, Hierarchie, primäre/sekundäre Aktionen, Zustände/Fehler, Mobile, Accessibility, API und Übergänge vorhanden. IEF-C01/C02 und IEF-08 übernehmen diese als gemeinsame Abnahmeverträge. Routerbasis, Legacyidentitäten, Back/Forward/Reload, Tastatur, Fokus, Textstatus und kleine Displays sind explizit geplant. Keine bloße optische Neusortierung des Supervisors.

Die richtige Zuordnung lautet jetzt J3 Strategieentdeckung und J4 freie Tech-Erholungsfrage. J4 beginnt mit starken Kursrückgängen und einer zu präzisierenden Ein-Wochen-Erholung; Quartalszahlen sind nur ein separates Zusatzbeispiel, keine erfundene Voraussetzung. J6 enthält Provider aus, Budget leer, stale Daten, fehlende Rechte, Unsupported, ungültigen Quantgrant, Paperpause sowie Sitzung und Unknown. Die zweite J6-Überschrift ist eine reine Strukturkorrektur und vom Hauptagenten zu einer Unterüberschrift zusammenzuführen.

## Product Review

**Ergebnis: tragfähiger Productentwurf unter Behebung UX-P2-01/02; keine zusätzliche P1. Keine Implementierungs- oder Nutzertestfreigabe.**

### Erstnutzer ohne Schlüssel

Die fremdverfassten Rootartefakte bieten einen echten durchgehenden Lösungsweg: IEF-O01 erlaubt überspringbare Präferenzen und Demo ohne Provider-/Brokerkonto; D1/D3 geben der lokalen Analyse eigene POST-/GET-Semantik und einen gespeicherten Befund; IEF-P03/P04 verlangen reproduzierbare Methoden und lokale Nutzung ohne Modell; IEF-A01 und IEF-06 machen die Analyse mit stabiler Identität auffindbar; IEF-C04 und der J1-Produktionsbuildtest prüfen den Gesamtweg. Lokale feste Erklärung von bestätigten Mengen, Cash, Exposure und Qualitätsgründen ist ein tatsächliches Ergebnis, auch wenn Marktwerte, Gewichte und Performance unbekannt bleiben. Kein Produktversprechen benötigt ein erfundenes Modell oder eine Backtestrendite.

Ein konkreter Abnahmetest muss alle Schlüssel fehlen lassen, externe I/O zählen (Erwartung null), eine explizite Demoquelle wählen und einen Analysebefund mit Quellen-/Methodenrevision nach Reload wiederfinden. Ein reiner Backendfixture-Erfolg ohne bedienbaren UIweg oder ein „Daten fehlen“-Hypothesendialog erfüllt den Demoanalyse-Endpunkt von IEF-T-O01 nicht. Entsprechend ist UX-P2-01 vor Abschluss der Planung zu korrigieren.

### Forschungsnutzen und ehrliche Grenzen

IEF-S01–03, IEF-A03, D4 und IEF-05/06 erhalten freie Nutzerabsicht statt automatischen Registryersatzes. Die Testbarkeit entscheidet, ob die konkrete Frage exakt, mit bewusst akzeptierter Ersatzhypothese oder derzeit gar nicht prüfbar ist. Ergebnisse werden mit Daten-/Kosten-/Modus-/OOSbindung gelesen. Das liefert ein nutzbares Researchprodukt, ohne fehlende Earnings-, Einzelaktien-, Holdings- oder historische Bewertungsfähigkeiten vorzutäuschen.

### Paper und Wiederaufnahme

IEF-G01–03, D5 und IEF-07 trennen Preview, menschliche Bestätigung, Riskprüfung, Annahme, Brokerstatus und Fill. BUY-/SELL-Bruttogrenzen, bestehende Nachkaufsperre, Nullable-Gewichte und Unknownreservation bleiben ehrlich. Forschungsresultate ändern keine Handelsrechte. J6, IEF-A04 und der Recovery-/CI-Plan stellen negative Wege als Kernproduktanforderungen dar. Keine externe Aktivierung oder automatische finanzielle Wirkung ist für einen produktiven lokalen Einstieg nötig.

### Scope und Evidenz

Der Plan bleibt auf vorhandener Python/PostgreSQL-/Angular-Materialarchitektur, mit additiven Readmodels und begrenztem neuem Erklärungszweck im vorhandenen Supervisor. ARP-10 bleibt getrennte externe Abhängigkeit; Offline-Evidenz ist keine produktive Providerfreigabe. Als verbleibende Produktschärfung sind allein die zwei dokumentierten Konsistenzbefunde offen. Die Anwendung wurde in diesem Propose-Review nicht gebaut oder bedient; die positiven Aussagen betreffen den überprüfbaren Plan.

## Nachprüfung nach Rootkorrektur und Baseline-Fortschreibung

Readback am 2026-11-23: Der Worktree steht nach `git rev-parse HEAD` auf `0442f29571e53b983e9e08f2180c4bd3536e0b6a`. Die obigen Abschnitte dokumentieren den historischen Erstbefund auf der früheren Planungsbaseline; insbesondere war ARP-10 dort tatsächlich noch als externe offene Voraussetzung beschrieben. Die aktuelle Nachprüfung ersetzt diese Statusaussage, ohne die ursprüngliche Reviewchronik umzuschreiben.

`design.md` D7 und `discovery.md` dokumentieren nun ARP-10/#105 als CLOSED sowie erfolgreiche veröffentlichte Gates [Research 45190975614](https://github.com/fjure/agentic-trading-platform/actions/runs/45190975614) und [Contracts 45190976026](https://github.com/fjure/agentic-trading-platform/actions/runs/45190976026). `docs/evidence/arp-10-integrated-readiness.md` wurde ergänzend gelesen. Dieses Subreview verifiziert den lokalen HEAD und die konsistent aktualisierten Evidenzverweise; den GitHub-Readback hat der Hauptagent ausgeführt. Technische ARP-Readiness ist damit kein offener IEF-Blocker mehr. Reale Provider-/Modell-/Credential-/Zweckfreigaben und der neue PORTFOLIO_EXPLANATION_V1 bleiben davon getrennt. Diese veröffentlichte Baseline ist keine IEF-Implementierungsabnahme.

**UX-P2-02 geschlossen:** IEF-02 benennt jetzt ausdrücklich J1-Onboardingteilstrecke und verweigert den Anspruch auf die vollständige Analyse-/Task-/Cockpit-Journey. IEF-T-O01/J1 bleiben bis IEF-04/06/08 und integrierter IEF-09-Abnahme offen. Die Paketabhängigkeit bleibt azyklisch und fachlich korrekt.

**UX-P2-01 Hauptkorrektur bestätigt:** J1 nennt explizite demoRunId oder getrennten vorhandenen Demobefehl, gebundenen Snapshot, ausdrücklich angefordertes POST portfolio-analyses, persistierte analysisId, Methodenrevision und Reload. J2 und S4 unterscheiden lokale Analyse, optionalen Erklärungszweck und weiterhin nicht implementierte autonome Rolle. Dies ist der gewünschte echte lokale No-Key-Analyseweg.

**Verbleibender Rest bei diesem Readback:** Der abschließende Erstnutzernachweis in user-journeys.md verweist noch auf J1 → J4 → Hypothesen-Testbarkeitsbeurteilung. S2 beschreibt Finanzdaten noch ausschließlich als Paperledgerdaten und S3 bleibt in Ziel/Hierarchie/API Paper-only. Diese drei Textstellen müssen vor endgültigem Schließen UX-P2-01 denselben expliziten Demo-/Fake-/Paperkontext und Demoanalyse-Endpunkt wiedergeben. Der Hauptagent wurde mit den exakten Stellen informiert. Keine neue P1.

### Abschließender Readback – beide Befunde geschlossen

Die drei Reststellen wurden erneut vom Datenträger gelesen und sind korrigiert: Der abschließende Erstnutzernachweis führt ausdrücklich von Demoauswahl/demoRunId über bestätigten synthetischen Snapshot und bewusste lokale Analyseaktion zum persistierten Qualitäts-/Bestandsbefund mit analysisId, Methoden-/Quellrevision und Reload; externes I/O muss null bleiben. S2 bietet „Portfolio lokal analysieren“ als kontextabhängige Primäraktion und bindet Finanzdaten an die ausdrücklich gewählte bestätigte Demo-/Fake-/Paperquelle. S3 beschreibt Ziel, Hierarchie und Leseports derselben getrennten Quellkontexte, DEMO stets mit demoRunId. S4 enthält bereits den eigenständigen lokalen POST-/GET-Analyseweg.

**Endstatus UX Review: UX-P2-01 geschlossen, UX-P2-02 geschlossen; keine offenen P1/P2. Endstatus Product Review: keine offenen P1/P2.** J3/J4 sind korrekt zugeordnet, alle zehn Pflichtscreens und die vollständige negative J6-Matrix bleiben erhalten. Der Erstnutzer ohne Schlüssel erreicht im konsistenten Plan eine echte lokale Analyse und ein wiederauffindbares Ergebnis. Dies bestätigt die Planungsqualität auf der aktualisierten veröffentlichten Basis0442f29, keine bereits ausgeführte IEF-Anwendung oder erteilte Provider-/Handelsfreigabe. Die historische Befundbeschreibung bleibt oben zur nachvollziehbaren Reviewspur erhalten.

### Begrenzte Nachprüfung – Designsystem und Expertenpersona

Die nachträglich ergänzten Abschnitte wurden gelesen: `ux-architecture.md` definiert gemeinsame semantische Theme-Tokens, die Abstandsskala4/8/12/16/24/32px, skalierbare Textrollen, Karten-/Navigationsbreakpoints, Mindestinteraktionsflächen, Kontrastziele, Fokus/Tastatur, reduzierte Bewegung und mobile/Zoom-Abnahme. Exakte Decimalwerte, Einheiten sowie zugängliche Chartalternativen bleiben erhalten. `personas.md` benennt P3 jetzt ausdrücklich als technische Expertin mit Advancedbedarf für Parameter, Revisionen, Experimente, Läufe, Budgets und Freigabebindungen. Advanced bleibt Offenlegung derselben Fachobjekte und begründet keine zusätzlichen Rechte. Diese begrenzten Ergänzungen sind mit dem bisherigen Review konsistent; keine neuen offenen P1/P2.
