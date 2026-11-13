# ARP-08 · Autorisierter Smoke-Harness

## Ziel

Synthetischen Einmal-Smoke in die Produktionsroute und ein eigenes Budget integrieren.

## Requirements und Abhängigkeiten

ARP-P06, ARP-P04. Blockiert durch ARP-04.

## Komponenten

services/llm_gateway/probe.py, services/runtime/agent_provider_controls.py, tests/integration.

## Akzeptanz und positive Tests

Emulator belegt exakte Modellbindung und Usage-Erfassung.

## Negative Tests und Security

Ohne separates Consent oder bei falschem Modell blockieren. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Redigierter Offline-Smoke-Nachweis mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
