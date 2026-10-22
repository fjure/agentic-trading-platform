# APW-06: eingefrorener Entscheidungs- und LIMIT-Replay

Prüfstand: 22.10.2026. Lokale synthetische Evidenz für REQ-APW-006/007/009,
T-APW-009/010/012 und den Skill `decision-engine-evaluation`.

Der vorhandene normalisierte 100-Fall-Corpus aus
`tests/integration/test_replay.py` bleibt unverändert. Die Uhr steht auf
`2026-10-12T14:00:10Z`; Features, Evidenzverfügbarkeit, Trace und Portfolio bleiben
eingefroren. Beide tatsächlichen lokalen Engines, `MockDecisionEngine` und
`DeterministicBaselineDecisionEngine`, verwenden dieselben Regeln und Inputs.
Der neue Test erhält keinen Broker-, Submit-, Credential- oder SQL-Port.

## Ergebnisse

| Prüfung | Beobachtetes Ergebnis |
|---|---|
| Gemeinsame Replay-/Jev-Shadow-Suite | 34 Tests bestanden; Laufzeit 2,95 s |
| Drei vollständige Wiederholungen | Identische Berichte, Entscheidungs-, Input- und Vorschlagshashes |
| Entscheidungen vor/nach Planung | Keine Änderung an Choice, Distribution, Confidence, Modell-/Fragen-/Promptversion, Inputhash oder Trace |
| Mock gegen Baseline | 0 Unterschiede; 100/100 Inputs und Inputhashes abgedeckt |
| Bestehender Corpus | 70 LONG, 30 ABSTAIN; keine Schemafehler in gesunden Antworten |
| Paperplanung desselben Corpus | 70 LIMIT-Vorschläge; bei allen 30 Enthaltungen kein Vorschlag |
| Fehlende/veraltete Paperproofs und Confidence unter 0,70 | Für alle 70 sonst zugelassenen Fälle kein Vorschlag |
| Timeout, ungültige Antwort, fehlende Antwort | Je 100/100 ABSTAIN, kein Vorschlag; Schemafehlerzählung 0/100/100 |
| Zusätzliche FLAT-Fälle | Ohne Bestand kein Vorschlag; mit 3,25 Anteilen und 0,25 reserviertem Exit höchstens 3,00 SELL |
| Jev-Shadow-Regressionsfälle | Gesunde synthetische Shadowantworten bleiben tatsächlich ABSTAIN; sieben bestehende Fehlerklassen erzeugen keine freigegebene Shadowentscheidung |

Die positiven LONG-Fälle verwenden dasselbe synthetische öffentliche
Paperproof-Profil: 100 USD Budget, versionierte Quote und BrokerCapabilities,
bestätigte Fractional-DAY-LIMIT-Kombination und reguläre Session. Mengen werden
nach unten auf höchstens 9 Nachkommastellen begrenzt. Kosten einschließlich 1%
Puffer bleiben unter Budget und verfügbarem Cash; der Positionswert bleibt unter
10% des Eigenkapitals. Der LIMIT-Preis erfüllt den Centtick, TTL höchstens 30 s.
Der SELL-Fall verwendet einen nach unten begrenzten Bidpreis und überschreitet
keinen unreservierten Longbestand. Enthaltung löst keine Ersatzstrategie aus.

## Versionen und Hashes

Entscheidungskonfiguration `rules.v1`, Modell `neutral-baseline.v1`, Prompt
`no-prompt.v1`, Fragen `neutral-questions.v1`; 100 verschiedene Inputhashes.
Planner `apw06-paper-limit-replay.v1`, `PaperLimitPolicy.version=1`:
TTL 30 s, Quotealter 30 s, Capabilityalter 120 s, maximaler Preisabstand 0,001,
Mengenpräzision 9, Puffer 0,01, Positionsgrenze 0,10, USD/LIMIT/DAY/REGULAR.

| Inhalt | SHA256 |
|---|---|
| Eingefrorener Corpus | `09aaaed9ab608bf4e340adfd5af6d7173bb3725e090cf7f2fb9a58d4fe4229fe` |
| Entscheidungskonfiguration | `6139dff1d15c12a0c850842fc90c14985194fc4e1e8fda19f9ff52bcbcff2816` |
| Unveränderte Entscheidungsausgaben | `830b69844bf5085d05337dee2cd5bcf63b0186d7eea8adf40630fd8d13a342cf` |
| Plannerkonfiguration | `d4ff06796b435f4bac7408b392c0caa23d51a7f8c96592c59d2fd5d1915e84fc` |
| LIMIT-Policy | `169b74661ee9b16dab581b3d71ad7deac1d25178e3ea1ba3a2dd5fa872a79b9e` |
| Geordnete Planungsinputs | `38777183f3a504d9ca78f2896c2ca902f9867348156da095fae0d38728fc7e7a` |
| Geordnete Vorschläge einschließlich Enthaltungen | `760a6227f13131539c3ab5741cbff351f413a1ed324f6912da1b6f147c1555f5` |

Alle aufgeführten Hashes sind im Test als feste Vergleichswerte gebunden und
mit `evaluation_summary()` aus `tests.integration.test_paper_limit_replay`
reproduzierbar. Die Hashfunktion
normalisiert die vollständigen Dataclasses einschließlich Decimal-/UTC-Werten;
Inputlisten behalten ihre Reihenfolge. Jeder Vorschlag trägt zusätzlich den
Hash seiner unveränderlichen `PaperPlanningInputs` und die Policy-/Proofversionen.

## Reproduktion und Grenzen

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_paper_limit_replay.py tests/integration/test_replay.py tests/integration/test_jev_shadow.py -q --tb=short --basetemp .tooling/output/apw06-replay
.venv/Scripts/python.exe -m ruff check tests/integration/test_paper_limit_replay.py
```

Reale Anbieterlatenz, Kosten, Abrechnung und Kalibrierung sind **nicht verfügbar**.
Es gibt keine realen Providerdaten oder gelabelten Handelsausgänge. Der lokale
deterministische Latenzwert 0 ist kein gemessener Providerwert. Synthetische
Jev-Antworten und deren bestehende Eignungslabels belegen weder Profitabilität
noch reale Modellgüte; Confidence steuert die Zulassung, nicht die Positionsgröße.

Die lokale Replayprüfung ist bestanden. Tatsächliche SQL-/Runtimeintegration,
fortlaufende Recovery bei Jev-Ausfall und vollständige APW-06-/Handelsfreigabe
bleiben eigene Prüfungen. Die neue Evidenz ersetzt keine Risk-, Reservations-,
Reconciliation- oder Executiongates und behauptet keine tatsächliche
Alpaca-Fractionalfähigkeit. Keine Brokerorders, Netzwerkprovideraufrufe oder
Credentialzugriffe wurden ausgeführt.
