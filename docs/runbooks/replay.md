# Lokales Replay und Entscheidungsvergleich

Replay verarbeitet ausschließlich unveränderliche normalisierte MarketStates und bereits gespeicherte typisierte Antworten. Es erhält weder BrokerAdapter noch Credentials, Ledger-Schreibzugriff oder einen externen Decision-Client. Baseline und Mock werden lokal ausgewertet; gecachte Providerantworten werden anhand ihres Inputs und ihrer Versionen geprüft, ohne einen Provider neu aufzurufen. Dies ist keine Jev-Adapterfreigabe.

## Reproduzierbare Prüfung

Im Repository-Root mit den gesperrten Entwicklungsabhängigkeiten ausführen:

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_replay.py -q
```

Die versionierte lokale Corpusfixture, eingefrorene UTC-Auswertungszeit, Baseline-/Mockregeln und deterministischen IDs müssen gemeinsam erhalten bleiben. Ein später korrigierter Input wird als eigener Case beziehungsweise neue Version behandelt; den gespeicherten Ursprung und frühere Antworten niemals überschreiben. Evidenz, die erst nach dem Auswertungszeitpunkt verfügbar war, darf nicht an die Entscheidungsquelle gelangen.

Die Python-API bietet `ReplayCase(case_id, state, evaluation_at)`, `ReplayConfig(version, rules, expected_model_id=None)`, `replay(cases, config, source="baseline"|"mock"|"cached")` und `compare(left, right)`. `cases` ist ein Tuple unveränderlicher Cases. Für Cache-Replay eine vollständige Zuordnung `case_id -> CachedResponse(DecisionResult)` über `cached=` übergeben. Eine konkrete erfasste Modellkennung wird mit `expected_model_id` ausdrücklich gebunden; sie startet keinen Provider. Cache-Expiry muss nach der eingefrorenen Auswertungszeit und höchstens um die konfigurierte `decision_ttl_seconds` später liegen. Directional Cacheantworten dürfen stale, konfliktbehaftete oder unvollständige Eingaben nicht freigeben.

Beispiel für den festen lokalen Vergleich:

```python
from packages.decisions.replay import compare, replay
from tests.integration.test_replay import CONFIG, corpus

baseline = replay(corpus(), CONFIG)
mock = replay(corpus(), CONFIG, source="mock")
comparison = compare(baseline, mock)
assert comparison.same_corpus and not comparison.differences
```

Die Fixture ist ein reproduzierbares Beispiel, kein historischer Produktionsdatensatz. `ReplayRecord.semantic_hash` unterscheidet fachliche Änderungen; `response_hash` erfasst zusätzlich IDs, gemeldete Latenz und Verbrauch/Kosten. `technical_changed_fields` weist solche Unterschiede getrennt aus. Corpusgleichheit und Versionsabdeckung immer gemeinsam mit dem Ergebnisvergleich prüfen. Summen gemeldeter Verbrauchs-/Kostenwerte sind keine Schätzung für Cases mit unbekannten Werten; die einzelnen Ergebnisse behalten deren null-Werte.

## Vergleichsregeln

- Drei Wiederholungen desselben Corpus und derselben Regeln müssen dieselben fachlichen Hashes liefern. Unterschiedliche Regeln und Antworten werden als separate Ergebnisse mit unterscheidbaren Versionen verglichen.
- Akzeptierte Antworten müssen dem bestehenden typisierten Entscheidungsvertrag entsprechen. Pflichtfehler, fehlende oder ungültige Antworten, Timeout und nicht freigabefähige Daten ergeben sichere Enthaltung beziehungsweise explizite Ablehnung vor Auswertung.
- Der Bericht zeigt Corpus-/Konfigurationshashes, Versionsabdeckung, Ergebnisabweichungen, Abstention, Schema-/Fehlerprüfungen und vorhandene Latenz-/Verbrauchsdaten. Nicht berichtete Kosten oder Verbrauch bleiben unbekannt. Lokal gemeldete deterministische Latenz ist keine Messung einer externen Providerverbindung.
- Kalibrierung benötigt gelabelte Entscheidungseignung; ohne solche Labels wird kein Brier-Wert erfunden. Synthetische Labels bewerten nur die lokale Fixture. Confidence ist weder Gewinnwahrscheinlichkeit noch ein Sizinginput.
- Vergleich oder Shadow darf keine Orders auslösen und keine Strategie automatisch aktivieren. Die in D-07 vorgesehenen Schwellen sind Prüfbedingungen, keine Freigabe für reales PAPER oder LIVE.

## Bei Abweichung

Zuerst Case-ID, Corpus-/Inputhash, Auswertungszeit und sämtliche Modell-/Konfig-/Prompt-/Frageversionen vergleichen. Fehlende Cachebindung, abgelaufene Antwort oder Zukunftsevidenz nicht durch einen Liveaufruf oder eine nachträgliche Anpassung der Uhr verdecken. Den betroffenen Case getrennt sichern und die Fehlerprüfung reproduzieren. Alte Corpus- und Antwortversionen bleiben erhalten; ein korrigierter Case erhält eine neue Identität.

Prüfevidenz und tatsächliche Ergebnisse stehen in `docs/evidence/ptv-14-replay.md`. Externe Jev-Protokolle, Liveantworten und Shadow-Zertifizierung folgen PTV-15 mit offiziellen Providerquellen und gesonderten Grenzen.
