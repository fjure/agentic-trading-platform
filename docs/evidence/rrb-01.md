# RRB-01 / #69: additive Forschungsverträge

Apply-Auftrag vom 27.10.2026. Basis main `f78b9a65ada7c6aa523e3cb06481f561889efad6`, Issuebranch `codex/ptv-69-retrospective-contracts`. Ausschließlich Vertrags-/Client-/Prüfwerkzeuge und Dokumentation; keine neuen Runtimeoperationen, Datenbankrechte, Broker-/Provider-/Jevaktionen oder Betriebsaktivierungen.

## Ergebnis und Traceability

REQ-RRB-001/004/006/008/012, T-RRB-002/008/010/011/013/020: explizite optionale Modusauswahl mit Strict-Default, geschlossene retrospektive Vorschau-/Experimentbindungen und drei authentifizierte additive Operationen. 83 Backtest-Komponenten sind in `contracts/schemas/v1/backtesting.schema.json` gespiegelt; 16 neue positive Beispiele prüfen Manifest, Vorschau, qualitativen Bericht, unbekannten Kalender und paginierte Lauf-/Ergebnisprojektionen. Methoden und Dimensionen sind versioniert; kein Reliability Score.

Bestehende Strict-Requests und Manifesttypen bleiben gültig. Retrospektive Requests benötigen Policy und genaue Assessmentreferenz; Strict-Readiness weist retrospektive Protokolle ausdrücklich ab. Explizite retrospektive Laufantworten erzwingen konsistenten Modus-/Policy-/Versions-/Snapshot-/Report-/Einschränkungskontext. Erfolgsantworten benötigen vollständige Publikationsreferenzen. Kein automatischer Fallback.

Additive Projektionen vermeiden Breaking Changes und falsche Belegbehauptungen: `retrospectiveManifest` statt einer Erweiterung der alten Manifestunion; `retrospectiveItems` in Summary-/Fillseiten bei leerem alten Strict-Array. Retrospektive Coverage behauptet bei UNKNOWN niemals CERTIFIED_ABSENT. Fills tragen `simulatedMarketCutoff` statt scheinbarer damaliger Beobachtung. Die Präzisierungen sind in Design D5 und Runbook dokumentiert.

## Tatsächlich ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| `.venv/Scripts/python.exe scripts/validate-contracts.py` | PASS: OpenAPI 3.1.2, 66 Operationen, 27 Schemadokumente, 26 bisherige Schema-Beispiele, 125 Responsebeispiele; zusätzlich 36 Readiness-/Sammlungs-, 7 Offlinebundle- und 16 retrospektive Beispiele; bestehende Negativchecks bestanden |
| `.venv/Scripts/python.exe scripts/check-contract-breaking.py --base-ref f78b9a65ada7c6aa523e3cb06481f561889efad6` | PASS, oasdiff 1.33.0: null Errors, drei strukturelle allOf-Warnungen; keine Flags zur Abschwächung |
| Redocly 2.62.1 mit `.tooling/contracts/redocly.yaml` | PASS ohne Warnung |
| `./scripts/check-angular-client.ps1 -WriteClient` | PASS: gepinnter Generator 7.26.0, zwei über alle 285 Dateien byte-/SHA256gleiche Generierungen, mechanisch geschriebener Client, TypeScript einschließlich aller neuen JSONfixtures bestanden |
| `.venv/Scripts/python.exe -m pytest tests/contracts -q -p no:hypothesispytest` | **1822 passed**, eine bestehende FastAPI/Starlette-TestClient-Deprecationwarnung |
| bestehende Backtest-/Readinessunitregression | **104 passed**, vor Domänenänderungen |
| neue unabhängige retrospektive Vertragsmatrix | **288 passed**; auch Bestandteil der vollständigen Suite |
| Abnahmestufung | **9 passed**; fehlende/doppelte Taskzuordnung blockiert, Abschluss RRB-06 erzwingt alle drei tatsächlichen neuen Routen |
| Angular 22.2.2 Produktionsbuild mit gepinntem Node 22.23.3 | PASS; bestehende Budgetwarnungen (initial 928.12 kB bei Warnschwelle 500 kB, Supervisor-CSS 7.12 kB bei 4 kB); keine Budgetänderung in diesem Paket |
| Ruff auf neuen/angepassten Stage-/Sync-/Negativtestdateien | PASS |
| `scripts/scan-source-secrets.py` | abschließend 1190 versionierbare Quelldateien, null Credentialmusterbefunde; keine privaten Stores gelesen |
| `./scripts/openspec.ps1 validate retrospective-research-backtesting --strict` | PASS |
| `git diff --check` | PASS |

Finaler OpenAPI-SHA256: `72a21c9661c7760775375df8282756b0187422ba0e42b7973b79c76934865923`. Die abschließende reine YAMLdarstellungserhaltung hat identischen geordneten AST vor/nachher (SHA256 `be542596432f92dce08395cd61b5b7739fedd1635dd9018c8f024448f15bd852`). `git diff --histogram` vermeidet irreführende Myers-Zuordnung wiederholter Schemablöcke. Unveränderte Readiness-Status-allOf-Regeln wurden zusätzlich gegen die Basis strukturell identisch geprüft; die oasdiff-Warnung ist kein tatsächliches Entfernen dieser Regeln.

## Review und korrigierte Befunde

Unabhängiger read-only Vertrags-/Safetyreview und separat implementierte Negativtests. Behoben: brechende Manifestunion, widersprüchliche Modusprojektionen, NO_ACTIONS bei unbekannter Bewertung, falsche Strict-Coverage und Beobachtungsfelder in retrospektiven Ergebnissen. Finaler Review meldet keine offenen Paketblocker. Die erste Generatorprüfung erkannte Set- statt JSON-Arraytypen bei uniqueItems; semantisch unveränderte bestehende not/not-Schemaform erhält Eindeutigkeit und korrekte generierte Arrays. Keine manuellen Clientänderungen oder Fixturecasts.

Die Windows-Sandbox blockiert lokale Socketpaare des bestehenden HTTP-TestClient (Faulthandler: `_fallback_socketpair`/`accept`). Derselbe Einzeltest besteht außerhalb der Sandbox; dort bestand anschließend die gesamte unveränderte lokale Vertragssuite. Keine Anwendungskorrektur oder Abschwächung deshalb. Der Hypothesis-Terminalsummary-Plugin wurde für diese reine Vertragssuite wegen eines separaten Windows-Handle-Diagnostikfehlers deaktiviert; keine Tests dadurch abgewählt. Domänentests liefen mit regulären Plugins.

## Grenzen und Folgelieferungen

Neue HTTPadapter/DTOs, Snapshot-/Hashreproduktion, tatsächliche SQL-/Runtimeisolation, Persistenz und Browserabläufe sind noch nicht implementiert und durch dieses Paket nicht zertifiziert. Nur die drei neuen Operationen sind bis RRB-06 von der exakten Routenpflicht gestuft ausgenommen; alle Bestandsrouten wurden in der vollständigen Vertragssuite geprüft. RRB-02..08 liefern diese Pfade und ihre echte Offlineabnahme. Providerrechte, reale historische Eignung, wissenschaftliche Zuverlässigkeit und Profitabilität bleiben unverifiziert.

Issue-Sync und native DAG-Read-backs stehen maschinenlesbar in `docs/development/github-retrospective-issue-sync.json`. Dieses Dokument ersetzt keinen erfolgreichen Push-/SHAvergleich. Fremde Änderungen an README.md und apps/dashboard/angular.json bleiben außerhalb des Paketcommits.
