# ARP-01 · Produktionsvertrag und Deploymentbasis

## Ziel

Additive Status-, Setup-, Probe- und Aktivierungsverträge sowie gesperrte Compose-Topologie ausliefern.

## Requirements und Abhängigkeiten

ARP-I02, ARP-K01, ARP-P01. Blockiert durch keine Pakete.

## Komponenten

contracts/http/control-api.openapi.yaml, infra/compose, services/control_api.

## Akzeptanz und positive Tests

Schema, generierter Client und gesperrter Start aus versionierter Installation.

## Negative Tests und Security

Alte Clients funktionieren, unvollständige Konfiguration bleibt gesperrt. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Vertragsbericht und Compose-Topologie mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.

## Normative Abnahme

Die Anbieterquellen belegen Modell- und API-Version, vollständige abrechenbare Obergrenze sowie die aktuelle Tarifrevision. Die Beispielrechnungen sind Katalogevidenz.
