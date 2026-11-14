# ARP-06 · NLP-Katalog und Auswertung

## Ziel

48 unabhängige Goldfälle, Scorer, isolierten KI-Review-Handoff und deterministischen Custodian für das autorisierungsgebundene Evaluationsharness erstellen.

## Requirements und Abhängigkeiten

ARP-E01, ARP-E02, ARP-E03. Blockiert durch ARP-01.

## Komponenten

tests/evaluation, docs/evidence.

## Akzeptanz und positive Tests

Versionierte Fixture-Auswertung liefert getrennte und reproduzierbare Kennzahlen.

## Negative Tests und Security

Fehlende Antworten und kritische Semantik-/Safetyverletzungen scheitern. Die [Sicherheitsgrenzen](../threat-model.md) und die bestehende Quant-/Risk-/Execution-Isolation gelten für die Abnahme.

## Definition of Done und Evidenz

Goldstandard-Reviewbericht mit belegter Unabhängigkeitsstufe sowie Custodian- und Scorer-Nachweis mit Quell-SHA, reproduzierbarem Prüfkommando und Ergebnis. Relevante Specs, Contract-/CI-Prüfungen und Issue-Read-back sind belegt.

## Normative Abnahme

48 unabhängige Goldfälle, je sechs Holdoutfälle pro Gruppe, getrennte Qualitätsmetriken und transparente Nenner. Paarweise Modell-/Promptregression, Wilson-Grenzen, kritische Einzelbefunde und Ausbaupfad 100/200 dokumentieren und mit Scorer-Orakeln prüfen.

Synthetische Reviewerantworten, Testschlüssel und Betreiber-Fixtures genügen für die technische Offline-Abnahme. Eine reale modellgebundene KI-Fachfreigabe braucht belegte getrennte Reviewläufe, geschützte Verwahrung, verifizierte Registrierung und eine eigene Betreiberentscheidung.
