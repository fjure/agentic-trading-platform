# ARP-03 · BYOK und widerrufbare Generationen

## Ziel

Privaten Research-Setup-Pfad und exklusiven Credential-Lifecycle integrieren.

## Requirements und Abhängigkeiten

ARP-K01, ARP-K02, ARP-P01. Blockiert durch ARP-01.

## Komponenten

services/setup, services/runtime/agent_provider_controls.py, infra/compose.

## Akzeptanz und positive Tests

Flüchtige Eingabe, Rotation und Generation im Produktprofil belegen.

## Negative Tests und Security

CSRF, Symlink, unberechtigter Mount und widerrufene Generation scheitern. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Synthetischer Credential- und Mountnachweis mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.
