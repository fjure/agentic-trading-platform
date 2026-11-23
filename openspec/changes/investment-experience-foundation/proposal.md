# Proposal

## Why

Das veröffentlichte Lab besitzt belastbare Research-, Quant- und Paperbausteine, verlangt aber vom Nutzer, technische Bereiche und Freigaben selbst zu verbinden. IEF schafft einen zusammenhängenden, verständlichen Zugang für private Einsteiger und Fortgeschrittene, ohne Evidenz-, Datenschutz- oder Handelsgrenzen zu verändern.

## What Changes

- Überspringbares lokales Onboarding mit versionierten Betreiberpräferenzen, ohne Konto-, Schlüssel-, Provider- oder Handelszwang.
- Persönliches Cockpit, Portfolio Hub und deterministische Portfolio Insights mit expliziter Datenqualität und optional gesondert freigegebener KI-Erklärung.
- Strategiekarten und faire Vergleiche aus bestehendem Register und belegten Ergebnissen; Forschungsstatus und Paperfähigkeit bleiben getrennt.
- AI Task Center als verständliche Projektion und begrenzte Erweiterung vorhandener Researchläufe, mit getrennten Daten-, Kosten-, Hypothesen- und Quantentscheidungen.
- Geführte bestehende Paper-LIMIT-Aktionen mit Vorschau, aktueller serverseitiger Prüfung, ausdrücklicher menschlicher Bestätigung und Recovery.
- Kohärente Material-Navigation, zehn Hauptansichten, Progressive Disclosure, Activity Timeline, responsive und zugängliche Zustände.
- Neun eigenständig prüfbare Arbeitspakete, additive Vertragsplanung, sechs durchgängige Abnahmereisen, native GitHub-Abhängigkeiten und unabhängige Reviews.

## Capabilities

### New Capabilities

- `investment-onboarding`: überspringbare persönliche Einrichtung und Betreiberpräferenzen.
- `portfolio-experience`: quellengebundene Portfolioansichten und deterministische erklärbare Erkenntnisse.
- `strategy-discovery`: verständliche Strategieinformationen, Vergleichbarkeit und Fähigkeitsnachweise.
- `investment-task-experience`: nutzerorientierte Aufträge auf bestehender Researchruntime und getrennte Entscheidungen.
- `guided-paper-experience`: sichere Führung durch vorhandene Paperaktionen ohne neue Handelsrechte.
- `investment-cockpit`: Navigation, Dashboard, Aktivitätsverlauf und zugängliche gemeinsame Darstellung.

### Modified Capabilities

Keine vorhandene Anforderung wird ersetzt oder abgeschwächt. Main-Specs und noch nicht konsolidierte APF-/ARP-/QSL-Deltas bleiben verbindliche Grundlagen; neue Fähigkeiten ergänzen die Erlebnis- und Projektionsgrenzen.

## Impact

Geplante Änderungen betreffen `apps/dashboard`, `services/control_api`, bestehende Portfolio-/Ledgerleseports, `packages/agent_research`, kanonischen HTTP-Vertrag, generierten Client, additive PostgreSQL-Migrationen und CI. Konkrete Ist-Pfade und Zielgrenzen stehen in discovery.md und den Modellentwürfen.

Basis ist ausschließlich veröffentlichter main `0442f29571e53b983e9e08f2180c4bd3536e0b6a`. Der erneute Remotecheck nach der Arbeitsunterbrechung bestätigte ARP-10/#105 abgeschlossen und sowohl Research integration45190975614 als auch Contracts45190976026 erfolgreich. Die zuerst untersuchte Basis0c26def und ihr Download-Rate-Limit sind historisch. ARP-10 ist eine erfüllte technische Voraussetzung, keine reale modellgebundene Betreiberfreigabe; diese bleibt gemäß Readiness-Matrix separat erforderlich.

Nicht im Umfang: Apply, LIVE, neue Broker-/Markt-/Newsintegration, autonome Orders, automatische Promotion, Riskänderungen, echtes Mehrbenutzersystem, öffentliche Anlageberatung oder ein ausgeführtes Daily Briefing. Ein späteres lokales deterministisches Briefing kann die neuen Projektionen lesen; externe Nachrichten verlangen einen eigenen Change. Diese Planung erteilt keine Provider- oder Handelsfreigabe.
