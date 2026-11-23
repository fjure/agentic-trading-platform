# Investment Experience Foundation — Abschlussbericht

## Grundlage, Ist und Lücken

Aktuelle veröffentlichte technische Baseline ist0442f29571e53b983e9e08f2180c4bd3536e0b6a. APF01–10, ARP01–10, QSL und Paperworkflows sind implementierte Bausteine; ihre tatsächlichen Nachweise und verbleibenden Betriebsgrenzen stehen in discovery.md. ARP-10/#105 wurde inzwischen geschlossen; beide Baseline-CIs sind grün. Reale modellgebundene Betreiberfreigaben sind weiterhin offen. Die vorhandene Angularoberfläche hat keinen Router, technische Funktionsinseln und keine vollständige Portfolio-Performanceprojektion.

## Zielbild und Nutzerführung

Das Personal Investment Cockpit verbindet bestätigtes Portfolio, Datenqualität, Researchideen, Strategien, Aufträge, offene Entscheidungen und Activity. Drei Entwurfspersonas (Einsteigerin, fortgeschrittener Forscher, technische Expertin) teilen dieselben fachlichen Objekte mit Progressive Disclosure. Sechs vollständige Journeys führen durch Einstieg, Portfolioanalyse, Strategieentdeckung, freie Erholungsfrage, Paperorder und Fehler/Recovery. Der Einstieg funktioniert ohne Schlüssel: explizite Demoquelle, lokale begrenzte Analyse, gespeicherte analysisId und Ergebnis nach Reload.

Navigation: Mein Überblick, Portfolio, Strategien, AI-Aufgaben, Aktivität, Daten und Betrieb. Zehn Screens sind in ux-architecture.md einzeln einschließlich Hierarchie/Aktionen/API/Zustände/Fehler/Mobile/Accessibility beschrieben; navigation-design.md enthält Flow und Legacykompatibilität. Ein gemeinsames Materialdesignsystem definiert Abstände, Typografie, Kontext-/Statusanzeigen und zugängliche responsive Bedienung.

## Domänenarchitektur

Portfolio: additive serverseitige Lesesicht auf explizite Demo-/Fake-/Paperquellen, konsistente Revisionen, Decimalstrings/UTC und Kennzahlenverfügbarkeit. Kein zweiter Ledger. Noch nicht belegte Marktwerte, P&L-/Return-/Drawdownhistorie, FX oder Kostenbasis bleiben nullable mit Gründen.

Intelligence: versionierte deterministische Qualitäts-/Bestandsbefunde; Gewichte und Konzentration nur mit vollständiger Grundlage. Optionale KI-Erklärung ist eigener minimierter Zweck im bestehenden Supervisor und benötigt getrennte Daten-/Kostenautorisierung. Keine erfundenen Risikoscores oder Renditen.

Strategien: verständliches Register mit Version, Grenzen und belegten historischen Resultaten, getrenntem Researchstatus/Papereligibility und ehrlichem Vergleich. Kein automatisches Ranking, keine Promotion und keine Ersatzstrategie für eine untestbare Originalfrage.

AI Task Center: Projektion vorhandener Researchruns plus synchroner lokaler Analysequelle, versionierte Intentmetadaten, reale13Laufzustände, nachvollziehbare Gründe/Entscheidungen/Budgets/Evidenz. Keine zweite Queue, kein generischer execute und keine als verfügbar dargestellte Zukunftsrolle.

Paper: additive servergebundene Vorschau und Einmalbestätigung im vorhandenen Human-/Risk-/Reservations-/Executionpfad. BUY-Bruttoobergrenze, SELL-Mindestbruttoerlös und fehlende optionale Gewichte sind korrekt unterschieden. UNKNOWN bewahrt Verpflichtung und Reservation, Statusabfrage ersetzt Blindresubmit. Agenten erhalten keine Brokerrechte; LIVE bleibt unerreichbar.

## Umsetzungspakete, Nachweise und Freigaben

Sechs Capability-Deltas enthalten20Requirements/33Szenarien. Neun offene Pakete:01Verträge/Routerbasis,02Onboarding,03Portfolio,04Insights/Erklärung,05Strategien,06Aufträge,07Paperführung,08Cockpit/Timeline,09integrierte Abnahme. Vollständige fachliche/technische Anforderungen, User Story, positive/negative Tests, UX, Safety, Evidenz und DoD liegen je Paket vor. issue-graph.md und github-issues.json dokumentieren Zuordnung und26direkte Abhängigkeiten; Status des tatsächlichen GitHub-Sync steht in planning-verification.md.

traceability.md verbindet alle Requirements, Szenarien, Journeys und Pakete. test-strategy.md verlangt Produktionsfrontend/API/temporäres PostgreSQL/Research/Quant/synthetische Provider-/Paperdoubles, Security/Recovery, Contract-/Clientintegrität und verbindliche CI-Manifeste. Fünf getrennte Reviewperspektiven sind in reviews/ dokumentiert; bestätigte kritische Planungsbefunde sind behoben. Striktes OpenSpec22/22 und Planstruktur/Graph/Linkchecks bestehen. Kein IEF-Laufzeittest wird behauptet.

Der konkrete Freigabestand, Issue-Links, Git-Remoteprüfung und eventuelle noch offene organisatorische Blocker stehen in planning-verification.md. Apply benötigt in jedem Fall einen neuen ausdrücklichen Nutzerauftrag. Diese Planung führt keine Anwendung, Provideranfrage oder Order aus.


## GitHub-Issues

| Paket | Issue |
| --- | --- |
| IEF-01 | [[IEF-01] Experience Architecture und API Contracts](https://github.com/fjure/agentic-trading-platform/issues/107) |
| IEF-02 | [[IEF-02] Guided Onboarding](https://github.com/fjure/agentic-trading-platform/issues/108) |
| IEF-03 | [[IEF-03] Portfolio Hub](https://github.com/fjure/agentic-trading-platform/issues/109) |
| IEF-04 | [[IEF-04] Portfolio Insights und begrenzte Erklärung](https://github.com/fjure/agentic-trading-platform/issues/110) |
| IEF-05 | [[IEF-05] Strategy Discovery](https://github.com/fjure/agentic-trading-platform/issues/111) |
| IEF-06 | [[IEF-06] AI Task Center](https://github.com/fjure/agentic-trading-platform/issues/112) |
| IEF-07 | [[IEF-07] Guided Paper Order](https://github.com/fjure/agentic-trading-platform/issues/113) |
| IEF-08 | [[IEF-08] Personal Cockpit und Activity Timeline](https://github.com/fjure/agentic-trading-platform/issues/114) |
| IEF-09 | [[IEF-09] Integrated Experience Acceptance](https://github.com/fjure/agentic-trading-platform/issues/115) |
