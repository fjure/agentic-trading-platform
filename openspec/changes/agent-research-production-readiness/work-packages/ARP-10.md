# ARP-10 · Integrierte Readiness-Abnahme

## Ziel

Saubere Installation, vollständige lokale Kette und Rollback wiederholbar abnehmen.

## Requirements und Abhängigkeiten

ARP-E01, ARP-E02, ARP-I01, ARP-I02, ARP-P06, ARP-P07. Blockiert durch ARP-05, ARP-06, ARP-07, ARP-08, ARP-09.

## Komponenten

infra/compose, docs/runbooks, docs/evidence.

## Akzeptanz und positive Tests

Releasekandidat führt alle Offline-Gates und dokumentierte Betreiberpfade aus.

## Negative Tests und Security

Fehlende reale Betreiberfreigabe belässt die Funktion gesperrt und den Live-Nachweis offen. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Readiness-Matrix mit getrenntem Betriebsnachweis mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.

## Normative Abnahme

Vollständige Offline-Pflichtfälle, getrennten autorisierten technischen Smoke und Fachfreigabe mit vollständigem Holdout und Regression je Bindung abnehmen. Ein fehlender Betreiberlauf bleibt separat offen.
