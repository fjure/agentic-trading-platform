# ARP-05 · Verbindliche APF-Integration-CI

## Ziel

Fast-, DB- und Browser-Gates mit versionierten Manifesten automatisieren.

## Requirements und Abhängigkeiten

ARP-I01. Blockiert durch ARP-01.

## Komponenten

.github/workflows, tests/e2e, tests/integration.

## Akzeptanz und positive Tests

Sauberer PR- und Release-Lauf erzeugt vollständige Ergebnisse.

## Negative Tests und Security

Absichtlich fehlende Umgebung oder Pflichtsuite wird rot. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

GitHub-Run und JUnit mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.

## Normative Abnahme

Pflichtfall-IDs und SHA-Artefakte im Aggregator erzwingen. Fehlende Umgebung, ausgewählte Skips, fehlende Pflicht-IDs und Artefakte blockieren. Scorer- und Smoke-Suiten werden durch ihre liefernden Pakete registriert.
