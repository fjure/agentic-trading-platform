# QSL-04 – rekursive Close-Signale im vorhandenen Ledgerkern

Stand: 2026-11-05. Lokaler Implementierungsnachweis für Task 2.1 / Issue #80.
Forschungsverhalten gemäß Design D2 und REQ-QSR-001/002, REQ-QEX-002/006/008.
Kein Fit, Provideraufruf, Brokerauftrag oder Aktivierungsrecht.

`indicators.py` implementiert endlich gesäte EMA und Wilder-RSI mit Decimal38,
ROUND_HALF_EVEN und den vorgeschriebenen Formelreihenfolgen ohne interne
Quantisierung. Der geschlossene `QuantSignalMachine` konsumiert ausschließlich
chronologische Baridentitäten und positive Decimal-Closes. Einmalige Initialisierung
berechnet Indikatoren, lässt das Target FLAT und erzeugt keine Ledgerereignisse.
Jede weitere Entscheidung verarbeitet genau einen neuen Close. Gleichheit der EMAs
ergibt FLAT; RSI verwendet inklusive Eintritts-/Austrittsschwellen und Hysterese.
RSI-Sonderfälle liefern 50/100/0.

`simulate_quant(definition, phase, candidate, validation_context=...)` verwendet
denselben `_simulate`-Fill-/Gebühren-/Slippage-/Cash-/Quantity-/Equitykern wie der
Bestand. Der optionale interne Maschinenport verändert keine Legacyberechnung.
Jeder Aufruf erzeugt frische Maschine, Cashbasis, Position und Pendingmap. Wiederholung
rekonstruiert den Zustand aus unveränderlichen Daten; kein serialisierter Programmzustand.
QSL-Entscheidungshashes binden auch den bereits konsumierten Präfix über eine
deterministische Hashkette; Outcomes gelangen ausschließlich in den Ledgerkern.

Der geschlossene `QuantValidationContext` baut das vollständige Manifest aus seinen
Phasendatasets erneut auf und verlangt Gleichheit von Definition, Phasenzugehörigkeit,
Snapshot und Revisions-/Metadatenreferenzen. Strict verwendet den bestehenden
Single-Seed-PITvalidator; retrospektiv bleiben Snapshot-/Revisions-/Policyprüfungen
erhalten. Ohne diesen Kontext wird QSL abgewiesen, ebenso mit Legacykontexten.
Kandidaten müssen Teil der Definition oder eine der zwei festen Referenzen sein.
Die gemeinsame Seedbreite W gilt auch für die Referenzen.

Alle fünf QSL-Versionen sind ausführbar; der Resolver bindet normalisierte lokale
Strategiefunktionen sowie vollständige `indicators.py`- und `simulator.py`-Artefakte
an feste Quellpins. Feste Dateinamen vermeiden den zyklischen Import bei der
Kataloginitialisierung. Abweichende Quellen werden vor Ausführung abgewiesen.
Legacy-Config-/Manifestpräimages und die enge Legacystrategieunions bleiben erhalten.
Die finalen Pins werden gemeinsam mit QSL-03/0035 integriert, bevor erstmals eine
QSL-Ausführung veröffentlicht wird; es gibt keinen nachträglichen Austausch einer
bereits veröffentlichten Version-1-Implementierung.

## Ausgeführte Prüfungen

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_quant_lab_strategies.py tests/property/test_quant_lab_signals.py tests/unit/test_backtest_simulator.py tests/unit/test_quant_lab_models.py tests/unit/test_backtest_models.py tests/unit/test_retrospective_dataset.py -q --tb=short
```

223 PASS in 23.60s. Enthalten sind unabhängige Handwerte EMA(2,3)=11.5/11 und
12.5/12, RSI(2)=50/75, gewichtete rationale EMA-/Wilder-Durchschnittsorakel,
monotone/konstante Randfälle, inklusive Schwellen, ungerundete Schwellenvergleiche,
Gleichheit, Hysterese, Seed ohne Targetwechsel, Duplicate-/Reverse-/Reseednegative,
fehlende Bars, Futureinputs, spätere Signaländerungen, Outcomeperturbation,
Snapshot-/Kontextverwechslung, beide Forschungsmodi und Replay bei fremder
ambienter Decimalpräzision/-rundung. Alle fünf Strategien durchlaufen jede der vier
frischen Phasen; der erste Equitypunkt bleibt jeweils in Cash ohne Seedfill.

Ruff für die fünf geänderten Pythondateien: PASS. Zusätzlicher unabhängiger
Direktvergleich gegen das aus `git show HEAD:packages/backtesting/simulator.py`
geladene unveränderte Bestandsmodul: identische vollständige Resultathashes:

- Buy/Hold: `1315bbf1eb23b50cc9ceb537eb85a52780dfdf6186b8ec780ec4fa746e25efaa`
- SMA mit Gebühren/Slippage: `04f5203a7b49e118e38faf94708cfcdb852772cbb8d5c46752c7a2547373ec3f`

Die bestehenden Literalhashprüfungen für Legacy-Strict und Legacy-Retrospective
Config/Dataset/Manifest sind Teil der erfolgreichen Regressionssuite.
Worker-/HTTP-/SQLintegration, globale Ressourcenmessung und Handels-/Marktvalidierung
sind durch diesen lokalen Signalnachweis nicht behauptet; sie gehören zu ihren
gesonderten QSL-Arbeitspaketen. Issue-Sync und geprüfte Gitintegration übernimmt
der Hauptagent.

## Abschließender unabhängiger Review

Der Signal-/Leakage-Review fand eine fehlende Bindung des Target-Enums im
Implementierungsfingerprint. Eine gleichnamige Version konnte bei veränderter
Enumquelle ein anderes Target liefern. Die Enumquelle ist jetzt Bestandteil
aller fünf Fingerprints; die Mutation wird durch einen dauerhaften Negativtest
abgewiesen. Alle fünf finalen Erstpins lösen ausführbar auf. Die unveröffentlichte
Migration 0035 übernimmt genau diese Pins erst nach QSL-04.

Finale Hauptagentenprüfung der Signal-, Property- und Legacy-Simulatorfälle:
**91 passed in 125.48s**, Ruff bestanden. Der unabhängige Review bestätigte den
Enumfix sowie frische Phasen aller fünf Strategien in beiden Modi: drei gezielte
Tests bestanden, keine offenen P1/P2 im Paketumfang. Die Legacy-Ergebnishashes
wurden zusätzlich unabhängig gegen den vorigen Simulator verglichen. Die
Laufzeiten der Tests sind keine Worker-Budget- oder Lastabnahme.

Implementierungscommit `9b9aba7d285aeb27e5854d3dbd8610e6556573aa` und
main-Merge `cdfe42c71c22f35a716ac66650af2951068250fe` regulär gepusht,
beide Remote-SHAs und identische Bäume verifiziert. Anschließend Taskcheckbox
geschlossen und #80 mit dem autorisierten GH-Fallback synchronisiert:
ein bestehendes Issue aktualisiert, acht unverändert, neun Read-backs und
19 native Kanten exakt bestätigt. Separater MCP-Read-back bestätigte #80
geschlossen und mit dieser Evidenz verknüpft.
