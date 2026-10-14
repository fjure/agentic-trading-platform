# PTV-14: Deterministisches Replay und Vergleich

Der lokale Replaypfad akzeptiert ausschließlich typisierte eingefrorene MarketStates mit Case-ID und UTC-Auswertungszeit, eine versionierte Regelkonfiguration sowie lokale Baseline-, Mock- oder bereits erfasste interne DecisionResults. Er erhält keine Provider-/Brokerfactory, keine Credentials und keinen Ledger-Schreibport. Corpus und Ergebnisse bleiben unveränderlich. Nested Zeitstempel von State, Bar, Evidence und Portfolio werden vor Auswertung gegen die eingefrorene Uhr geprüft; spätere Verfügbarkeit ist nicht freigabefähig.

Das feste synthetische `corpus.v1` in `tests/integration/test_replay.py` enthält 100 Cases, darunter stale, konfliktbehaftete und unvollständige Inputs. Drei gleiche Durchläufe prüfen Reproduzierbarkeit. Die Fixture trägt keine gelabelten externen Outcomes; Kalibrierung bleibt unbekannt. Lokale Baseline-Latenz0 ist gemeldete deterministische Metadaten und kein Providerbenchmark. Nicht vorhandener Verbrauch und Kosten werden nicht zu0 umgedeutet.

| Requirement / Test-ID | Nachweis |
| --- | --- |
| REQ-MKT-004 / T-MKT-004 | State-, Bar-, Evidence- und Portfoliozeit nach Replayzeit wird vor lokaler Auswertung zurückgewiesen |
| REQ-DEC-001 / T-DEC-001 | Korrektur erhält eigenen Inputhash; alter Corpus und gespeicherte Version bleiben unverändert |
| REQ-DEC-006 / T-DEC-006 | Konfig-/Modell-/Prompt-/Frageversionen, typisierte Antwort, Verteilung, Confidence, Inputhash, Trace, TTL und gemeldeter Verbrauch bleiben nachvollziehbar |
| REQ-DEC-007 / T-DEC-007 | Drei identische Replays und lokaler Baseline-/Mockvergleich; keine Broker-/Provideraufrufe |

Endgültiger Prüfstand: 19 Replaytests; zusammen **128 Contract-/Domain-/Broker-/Risk-/Replayprüfungen passed in3.06s**. Ruff, strikte OpenSpec-Validierung und Source-Scan268/0 bestanden. Der unabhängige Review fand und verifizierte die Korrekturen für Cacheadmission, explizite Provider-Modellbindung, Corpus-/Semantikvergleich und begrenzte Cache-TTL; Abschlussreview ohne offene Befunde. Der Cache akzeptiert Richtungsergebnisse nur mit aktuell admissiblem Input, ausreichender Confidence und eindeutig passender Richtungsverteilung. Er prüft Input-/Konfig-/Modell-/Prompt-/Fragebindung und verlangt `evaluation_at < valid_until <= evaluation_at + decision_ttl_seconds`. Abgelaufene oder überlange Antworten werden zurückgewiesen, auch ABSTAIN.

Der fachliche Hash enthält Verhalten, Versionen, Input, Trace und TTL; ID, gemeldete Latenz, Verbrauch und Kosten stehen getrennt im vollständigen Responsehash. Der Vergleich weist beide Corpora, deren Gleichheit, Caseabdeckung und Konfig-/Modell-/Prompt-/Frage-/Inputversionen aus. Technische Unterschiede bleiben separat sichtbar. Unterschiedliche oder umgeordnete Corpora werden nicht als derselbe Inputbestand dargestellt.

Der lokal erzeugte Prüfbericht `.tooling/output/ptv-14-report.json` enthält Baseline, Mock, normalisierte Cacheantworten, drei Wiederholungen, Regelversionsvergleich und drei Fehlerläufe über je100Cases. Kein erforderliches Sourceartefakt hängt von dieser ignorierten Datei ab; der Corpus und die Prüflogik sind im versionierten Test festgelegt. Bericht-SHA256: `64be710a262099972f5ace4e57011a0d80edeb458fd6abe42e835e91f4d18c3a`. Corpus-SHA256: `09aaaed9ab608bf4e340adfd5af6d7173bb3725e090cf7f2fb9a58d4fe4229fe`. Konfig-SHA256: `5eb5b7d752b8607ec10c736d793a406a6fd88b4487bab4427e6a69adfbba2c4d`.

Ergebnis:100Cases, drei gleiche Durchläufe,0 Baseline-/Mockabweichungen und0 Baseline-/Cacheabweichungen. Normale Abstentionrate0.30 (30stale/konfliktbehaftete/unvollständige Inputs); gemeldete p50/p95-Latenz0ms. Bei timeout/invalid_response/missing_response jeweils100/100 sichere ABSTAIN. Akzeptierte interne Ergebnisse sind typisierte DecisionResults;0Schemafehler im normalen Corpus. Kalibrierung, Verbrauch und Kosten bleiben null. Dies ist kein externer Provider-Schema- oder Latenznachweis. HTTP, JSON-Schemas und generierter Angular-Client bleiben unverändert.

Externe Jev-Wireantworten, Liveanbieter, Shadow-Zertifizierung, reale Kalender und strategische Promotion sind nicht durch diesen lokalen Nachweis freigegeben. Der Cache enthält bereits normalisierte interne DTOs; keine unbelegten offiziellen Anbieterprotokolle werden angenommen. Runbook: `docs/runbooks/replay.md`.
