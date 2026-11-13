# ARP-01 · Produktionsvertrag und Deploymentbasis

## Ziel

Additive Status-, Setup-, Probe- und Aktivierungsverträge sowie gesperrte Compose-Topologie ausliefern.

## Requirements und Abhängigkeiten

ARP-P01, ARP-K01, ARP-I02. Blockiert durch keine Pakete.

## Komponenten

contracts/http/control-api.openapi.yaml, infra/compose, services/control_api.

## Akzeptanz und positive Tests

Schema, generierter Client und gesperrter Start aus versionierter Installation.

## Negative Tests und Security

Alte Clients funktionieren, unvollständige Konfiguration bleibt gesperrt. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Vertragsbericht und Compose-Topologie mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
