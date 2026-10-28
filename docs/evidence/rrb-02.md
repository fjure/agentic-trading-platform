# RRB-02 / #70: getrennte revisionsgebundene Datenauswahl

Stand 28.10.2026. Gesonderter Apply-Auftrag liegt vor. Ausgangsstand: main `4573a9a697aa0c27eb440823f256d5816f8c3760` mit abgeschlossenem RRB-01. Issuebranch `codex/ptv-70-retrospective-dataset`. Dieses Paket liefert reine Domainmodelle und Auswahlregeln; noch keine neue Worker-/HTTPausführung.

## Implementiertes Verhalten

REQ-RRB-001..004 und T-RRB-001..008: geschlossene versionierte Strict-/Retrospektivpolicies; der bisherige Konfigurations-/Manifestaufbau bleibt unverändert. Strict-Materialisierung akzeptiert nur ihren bestehenden FrozenDataset-Typ. Der neue Resolver hat keinen Lockerungsschalter und verwendet unveränderte BarRevision-Objekte.

Retrospektive Sichtbarkeit bindet Commitversion, tatsächliche Aufnahme und tatsächliche Verfügbarkeit an denselben Snapshot. Neuere ungültige Heads erlauben keinen stillen Rückgriff auf ältere gültige Bars. Mehrdeutige Revisionsnummern, fehlende oder manipulierte Referenzen und unbekannte Kalenderfenster blockieren. Revisionen und Kalender lassen sich anhand exakter gebundener Referenzen wiederherstellen, ohne spätere Antworten als Ersatz auszuwählen.

Warm-up und vollständige Signal-/Outcomefenster werden kalendergebunden geprüft; jedes Signal erhält nur seine abgeschlossenen Marktpreisereignisse. Nächste Fillziele beginnen frühestens nach der bestehenden +60s-Signalgrenze. Die spätere Fillkerze ist kein Eingangssignal. Heutige Kalenderinhalte mit verifiziertem Inhalt dürfen ohne erfundenen historischen Zeitbeweis benutzt werden; negative/widerrufene Inhalte bleiben blockiert.

Modus, Policy-/Experimentversion, Konfiguration, Snapshot, Build, Truststand, Kalender-/Bar-/Qualitäts-/Maßnahmenreferenzen und zwingender Lizenzreceipt-Fingerprint binden die neue Manifestidentität. Revisionsreferenzen bewahren echte Ingestion/Verfügbarkeit; firstObservedAt bleibt ohne echten Empfangsnachweis null. Getrennte simulierte Bewertungs-/Wissenszeiten sind keine lokalen Beobachtungen.

## Tatsächlich ausgeführte Prüfungen

- Hauptagent: **143 passed** für neue Dataset-/Legacyhash-Tests und bestehende Modelle, Dataset-, Simulator-, Evaluation-, Metrik-, Request- und Readinessunitregression.
- Autor: **98 passed** mit `pytest tests/unit -q -k 'backtest or retrospective'`; weitere unabhängige Tests blieben dabei ausdrücklich deselected.
- Unabhängiger read-only Review: **39 passed** für `test_retrospective_dataset.py` und `test_retrospective_legacy_hashes.py`; keine offenen Korrektheits-/Safetyblocker im reinen Domainumfang.
- Ruff auf neuen/angepassten Domain- und Testdateien bestanden. Keine generierten Dateien oder HTTPverträge in diesem Paket verändert.
- Feste, vor den Domainänderungen zweimal erfasste Strict-Konfigurations-, Manifest-, Simulations- und Evaluationshashes bleiben exakt erhalten; die Assertions sind in `tests/unit/test_retrospective_legacy_hashes.py` versioniert.

Die neue Matrix prüft reale Zeitfelder ohne Rückdatierung, beide Snapshotgrenzen und Verfügbarkeit, spätere Korrekturen/alte Wiederholung, lokale Revisionsties, neuere ungültige Heads, fehlende/falsche Referenzen, Zukunftsperturbation, Warm-up-/Kalender-/Fillgrenzen, Policyverwechslung, negative Trustmarker, mutierbare Eingaben und gefälschte gleichnamige Barvorkommen. Budgetnegativfälle belegen die gemeinsame 100000-Zeilengrenze sowie Ablehnung oberhalb der 12MiB-Publikationsgrenze und Abbruch über den Checkpoint.

## Reviewkorrekturen und Grenzen

Korrigiert wurden eine unzulässige Gleichsetzung von Empfang und Ingestion, fehlende Verfügbarkeitsgrenze am Snapshot, Rückfall nach einem ungültigen Head sowie redundante vollständige Kerzenkopien im Hashpräimage. Kompakte Referenzbindungen werden nun gehasht und die JSONausgabe wird ohne Zusammenfügen eines übergroßen Strings begrenzt. Der kanonische Referenzgraph wird zuvor unter der Zeilengrenze aufgebaut; 12MiB ist keine Behauptung einer gleich großen maximalen Heapallokation. Der unabhängige Review bewertete dies als nicht blockierenden Ressourcenhinweis; Runtimegrenzen und Lastabnahme folgen später.

Unveränderliche SQLspeicherung und tatsächliche Commitrennen zertifiziert RRB-03; Qualitäts-/Kapitalmaßnahmenentscheidung RRB-05; gemeinsame Simulation RRB-04; Transport RRB-06. Dieses Paket bestätigt weder persönliche Providerrechte noch reale historische Verfügbarkeit oder wissenschaftliche Verlässlichkeit und aktiviert keinen Betrieb. Source-/OpenSpec-/Issue-/Gitabschluss werden vom Hauptagenten vor Integration getrennt geprüft. Fremde README-/Angularänderungen und parallele RRB-03-/04-Dateien gehören nicht zu diesem Commit.
