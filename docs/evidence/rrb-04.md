# RRB-04 / #72: gemeinsame chronologische Simulation

Stand 28.10.2026. Gesonderter Apply-Auftrag liegt vor; Basis main `662693276afae7b4b6d452b8ae6e2c0b8ab9d67a`, Issuebranch `codex/ptv-72-retrospective-simulation`. RRB-02 ist integriert; dieses Paket benötigt keine neue Persistenz oder Runtime.

## Verhalten und Prüfung

REQ-RRB-001/003/004/007, T-RRB-001/005/006/007/008/011/012: `simulate` und `evaluate` verwenden weiterhin denselben Decimal-Rechenkern für Strategien, Fills, Kosten, Cash, Equity, Benchmarks und Foldmetriken. Ohne Kontext ist ausschließlich ein echter Strict-FoldDataset zulässig. Retrospektiv wird ausschließlich der geschlossene RetrospectiveValidationContext mit passender Konfiguration, Policy, Snapshot, Build, Trust und Manifest-/Datasetbindung akzeptiert.

Öffentliche Simulation validiert das gesamte Manifest vor dem ersten Checkpoint. Evaluation prüft es einmal vor der Foldschleife und verwendet danach denselben privaten Rechenkern mit erneuter exakter Datasetbindung je Simulation. Kein öffentlicher Lockerungsschalter oder fremdes Strategieprogramm. Retrospektive Outcomegrenzen bezeichnen den simulierten Marktschluss (`bar_end`); Bewertungsmethode `raw-iex-close-retrospective-market-v1`. Echte Aufnahme-/Verfügbarkeitszeiten werden nicht verändert. Strikte +60s-/+300s-Prüfungen bleiben erhalten; keine gestartete Fillkerze oder zukünftige Outcomes gehen in das Signal ein.

- Autor: 36 fokussierte Tests und 118 kombinierte Domainregressionen bestanden; Ruff auf sechs eigenen Dateien sauber.
- Unabhängiger Review: 35 Tests bestanden, keine offenen P1/P2. Die im Review gefundene fehlende vollständige Manifestprüfung im direkten simulate-Aufruf wurde korrigiert und erneut geprüft. Drei Negativtests verändern Policy/Build/Trust und belegen Ablehnung vor Checkpoints.
- Hauptagent: dieselben 35 Simulator-/Evaluation-/Storagefixture-/Legacyhashtests mit eigenem Workspacebasetemp erneut bestanden (1.73s); Ruff sauber.
- Vier wörtliche Strict-Hashes für Konfiguration, Manifest, Simulation und Evaluation bleiben unverändert. Neue Arithmetiktests prüfen handberechnetes Cash, Fees, Drawdown, Benchmark und Foldreset, Zukunftsperturbation, Zeitgrenzen und Policy-/Subklassenverwechslung.

Drei bestehende Testfixtures wurden von mutablen Hilfsobjekten auf die echten unveränderlichen Foldmodelle beziehungsweise dataclasses.replace umgestellt. Bestehende Werte, Assertions und Hashorakel bleiben erhalten. Keine Strategien, Verträge, generierten Clients, Storageimplementierung oder Handelsrechte verändert. Ein geschlossener unveränderlicher Bindungsgraph bleibt Voraussetzung; keine Ausführung beliebiger Nutzerprogramme.

Worker-/HTTP-/Browserintegration und Runtimeisolation werden erst in RRB-06..08 abgenommen. Dieses Paket aktiviert keinen Dispatcher und bestätigt keine historische Verfügbarkeit, Providerrechte oder Profitabilität.
