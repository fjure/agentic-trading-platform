## Why

Das Quant Strategy Lab kann Hypothesen reproduzierbar untersuchen. Für Einsteiger fehlen ein geführter Einstieg und verständliche Ergebnisse, für Betreiber eine begrenzte, nachvollziehbare Plattform für modellgestützte Forschung und regelmäßige neue Ideen.

## What Changes

- Einen Research Companion mit persistentem Dialog, versionierten Ideen, begründeter Testbarkeit, geführtem und technischem Zugang sowie evidenzgebundenen Berichten spezifizieren.
- Historische Revisionen und persistierte Researchbefunde browserunabhängig prüfen. Nur relevante neue zulässige Evidenz löst begrenzte Modellinterpretation aus.
- Geschlossenes Agenten- und Toolregister, persistente Läufe, deterministischen Supervisor, Budgets und betreibergebundenen Quant-Start ergänzen.
- Provideradapter für OpenAI und Anthropic mit isoliertem Gateway und separat zu genehmigender BYOK-/Egress-Grenze planen.

## Capabilities

### New Capabilities

- `agent-research-runtime`: Agenten, Tools, Läufe, Budgets und Freigaben.
- `agent-provider-boundary`: Provider, Schlüssel, Datenausleitung und Isolation.
- `research-companion`: Ideen, Dialog, Guided/Advanced und Bericht.
- `proactive-research`: Zeitplan, Trigger, Publikation und Wiederholung.
- `agent-research-control`: Authentifizierte API und Angular-Material-Oberfläche.

### Modified Capabilities

Keine Verhaltensänderung für bestehende Aufrufer. Der Agentenpfad bindet die bestehende Quant-Zulassung zusätzlich an eine explizite Freigabe.

## Impact

Python/PostgreSQL und Angular Material mit additivem OpenAPI-Vertrag. Quant Lab, Researchdaten und Simulator werden weiterverwendet. Research bleibt Autonomiestufe 0. Handels- und Riskrechte, LIVE, Portfolio Planner, dynamischer Strategiecode, News-/Fundamental-/Makroquellen und ein lokaler Modelladapter liegen außerhalb von v1. Dieser Change ist Planung. Apply und neue Providerrechte erfordern eigene Freigaben.
