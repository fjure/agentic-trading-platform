# ARP-02 · Gatewaytransport und Egress

## Ziel

Produktive HTTP-Komposition, signierte Dispatch-Grenze und dedizierten CONNECT-Egress verdrahten.

## Requirements und Abhängigkeiten

ARP-P02. Blockiert durch ARP-01.

## Komponenten

services/llm_gateway, infra/compose, tests/security.

## Akzeptanz und positive Tests

Beide Providerwires durch reale TCP/TLS-Komposition im Testnetz.

## Negative Tests und Security

Falsche Ziele, Redirects, Zertifikate, DNS-Antworten und alte Fences blockieren. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Wire- und Netzisolationsbericht mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
