# ARP-09 · Security und Recovery unter Last

## Ziel

Produktionskomposition und deterministische Crash-/Widerrufs-/Lastfälle im Gate absichern.

## Requirements und Abhängigkeiten

ARP-I02, ARP-K02, ARP-P02, ARP-P04. Blockiert durch ARP-04, ARP-05.

## Komponenten

tests/security, tests/recovery, .github/workflows.

## Akzeptanz und positive Tests

Reconciliation und Kill bleiben innerhalb des Safetybudgets.

## Negative Tests und Security

Doppelversand, Doppelstart, stale Fence und Ausbruchversuche scheitern. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Isolations-, Last- und Recovery-Bericht mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
