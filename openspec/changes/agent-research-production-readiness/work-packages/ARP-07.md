# ARP-07 · Dashboard und Betreiberablauf

## Ziel

Material-Setup, Providerbereitschaft, Sendefreigabe und proaktiven Serverstatus umsetzen.

## Requirements und Abhängigkeiten

ARP-K01, ARP-P02, ARP-P03, ARP-P05, ARP-P06, ARP-P07. Blockiert durch ARP-04, ARP-08.

## Komponenten

apps/dashboard, docs/runbooks.

## Akzeptanz und positive Tests

Operator führt Setup, Preview, lokale Klärung und Statusabfrage aus.

## Negative Tests und Security

Deaktivierte Profile und Zeitpläne erscheinen gesperrt, Schlüssel werden nicht persistiert. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Browsernachweis und Betreiber-Runbook mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.

## Normative Abnahme

Die UI zeigt modellabhängige Reserve, Tarif, Betreiberlimit, Sperrgrund und getrennte Freigaben. Ein Reload sendet weder Smoke noch Research erneut.
