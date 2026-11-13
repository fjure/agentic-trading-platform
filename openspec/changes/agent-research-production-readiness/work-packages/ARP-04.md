# ARP-04 · Profile, Freigaben und Kosten

## Ziel

Produktive Supervisor-Komposition mit Profilepoch, exakter Preview, proaktivem Grant und hierarchischem Ledger vervollständigen.

## Requirements und Abhängigkeiten

ARP-P01, ARP-P03, ARP-P04, ARP-P05, ARP-K02. Blockiert durch ARP-02, ARP-03.

## Komponenten

services/runtime/agent_service.py, packages, services/control_api/migrations.

## Akzeptanz und positive Tests

Aktuelle Rechte und Reservierungen sind bis zum veröffentlichten Ergebnis gebunden.

## Negative Tests und Security

Parallele Überschreitung, unknown, alte Rechte, Doppelversand und unzulässige Daten blockieren. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

PostgreSQL- und Race-Evidenz mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
