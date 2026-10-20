# Proposal

## Why

Das laufende Dashboard zeigt im sicheren Fake-Lab keine Trades. Der vollständige Handelsdurchstich wird bisher in einer nach dem Test entfernten Datenbank geprüft; Nutzer können die Funktion deshalb nicht an der Oberfläche nachvollziehen.

## What Changes

- Im bestehenden lokalen Dashboard einen sichtbaren Einstieg „Demo starten“ anbieten, ohne Brokerkonto, zusätzliche Terminals oder manuelle Handelsfreigaben.
- Einen endlichen, reproduzierbaren synthetischen Handelsdurchlauf mit FakeBroker und MockDecisionEngine ausführen; Entscheidung, Riskfreigabe, Order, Teil-/Endfill, Portfolio und Audit über die tatsächliche Anwendung sichtbar machen.
- Demo dauerhaft als Simulation kennzeichnen und vollständig vom bestehenden Laborledger sowie von Paper-Providern trennen.
- Mehrfachklick, Fehler und Wiederholung sicher behandeln; keine Fake-Trades direkt als fertige UI-Objekte einblenden.
- Nutzerfreigabe 2026-10-18: Alpaca-Paper- und Jev-Einrichtungsdialoge mit flüchtiger menschlicher Schlüsseleingabe und getrenntem privatem Speicher ergänzen. Speichern aktiviert weder Provider noch Handel.
- Frei wählbares exaktes USD-Demostartbudget und separates begrenztes Paper-Handelsbudget sowie Auswahl der vorhandenen Mock-/Baselinevarianten anbieten. USD und vorhandene Varianten wurden ausdrücklich bestätigt; keine EUR-Umrechnung oder neue Strategie vorgesehen.

## Capabilities

### New Capabilities

- `dashboard-demo`: Einfache sichtbare, isolierte Handelssimulation mit prüfbarer Ende-zu-Ende-Ergebniskette.
- `provider-setup`: Lokale geschützte Einrichtung von Alpaca-Paper und Jev sowie konfigurierbare USD-Budgets und bestehende Entscheidungsvarianten.

### Modified Capabilities

Keine bestehenden Main-Specs vorhanden. Die noch aktiven Sicherheitsanforderungen des Changes `paper-trading-vertical-slice` bleiben maßgeblich und werden nicht abgeschwächt.

## Impact

Kanonischer HTTP-Vertrag und reproduzierte Angularclientgenerierung; Control API, getrennte Einrichtung und Demoausführung, lokale Provisionierung/Compose, Angular-Material-Supervisor, Integration-/Browserprüfungen und README. Keine Agentenzugriffe auf echte Providercredentials, LIVE-Freigabe oder Änderung bestehender Kontobasen. Ein echter Paperlauf bleibt ein separater menschlicher Ablauf. Apply wurde ausdrücklich freigegeben.
