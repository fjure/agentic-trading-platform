# Proposal

## Why

Die offline abgenommene Agent Platform Foundation verbindet Researchdialog, Supervisor, Quant Lab und Dashboard. Für den kontrollierten lokalen Betrieb fehlen der reale Providerpfad, ein verbindliches Integrationsgate und ein modellgebundener Nachweis der deutschsprachigen Interpretationsqualität.

## What Changes

- Optionale, standardmäßig gesperrte OpenAI- und Anthropic-Profile mit lokal verwalteten Schlüsseln, getrennten Freigaben, zweckgebundener Verarbeitung und konservativer Kostenabrechnung.
- Reproduzierbare CI-Gates für Datenbank, Agentenruntime, Gateway, Quant Lab, Browser, Security und Recovery.
- Versionierte deutschsprachige Evaluation mit unabhängig bewerteten Referenzen und gesondert autorisierten Modellläufen.

## Capabilities

### New Capabilities

- `research-provider-activation` bindet Identität, Berechtigungen, Kosten und kontrollierten Egress.
- `research-integration-assurance` definiert ausführbare Integrations- und Release-Gates.
- `research-language-evaluation` definiert reproduzierbare semantische Abnahme.

### Modified Capabilities

- `provider-setup` erhält einen separaten lokalen Research-Schlüssellebenszyklus.

## Impact

Additive Änderungen an OpenAPI, Setup, Research-Supervisor, LLM-Gateway, Compose, Dashboard und CI. Die implementierten APF- und QSL-Deltas bleiben fachliche Grundlage. Quant Admission, Broker-, Risk- und Executionrechte bleiben unverändert. Die Planung erteilt weder Apply- noch Providerbetriebsfreigabe.
