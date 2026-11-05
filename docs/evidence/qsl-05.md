# QSL-05 · Chronologisches Auswahlprotokoll

Implementierungsnachweis für Task 2.2 / Issue #81. Abgedeckt sind Design D3,
`selection-policy.md`, `research-protocol.md` und die Auswahlteile aus
`metrics-methodology.md`.

`experiment_protocol.py` prüft die vollständige deklarierte Suchmenge anhand von
sieben Gates je Validationzelle. Die Nettorendite ist das Standardziel unter der
expliziten Drawdown-Grenze. Die zweite Policy sortiert zuerst nach Drawdown. Beide
vergleichen exakte Decimal38-Werte. Die Finalauswahl prüft jeden Fold einzeln und
sortiert nach den Komponentenmedianen. Zwölfstellige Half-even-Anzeigen stehen
als separate Projektion bereit. Integercounts bleiben pro Validation exakt.

Der Selektorport nimmt typisierte Validationprojektionen entgegen. Deren
Fingerprints stammen aus den gebundenen SEARCH-Validationresultlinks. Die
kanonische Selectionidentität bindet den vorhandenen Methodenfingerprint, die
Policy, den Scope, die sortierten Linkfingerprints und den fachlichen Wire-Inhalt.
`frozenAt` ist operative Evidenz. Der OAS- und der JSON-Schema-Vertrag akzeptieren
die erzeugten Selection-DTOs. Vergleichbarkeit setzt einen gemeinsamen gebundenen
Manifeststand und die passende Evidenzachse voraus.

Der Phasenplan gibt sämtliche Foldentscheidungen und die Finalentscheidung als
gemeinsames unveränderliches Artefakt aus. Bei technischem Teilfehler gibt er keine
OOS- oder Holdout-Phasen frei. Dokumentierte Nichtwahl lässt Referenzarbeit zu und
kennzeichnet alle Suchkandidaten als `NOT_EVALUATED_NO_SELECTION`. In-Sample
Analysis umfasst die vorab deklarierte Suche vollständig. `NOT_TRAINABLE` und die
fachlichen Rollen stammen aus den bestehenden Definitionen.

Der Expositionsvergleich findet rollenübergreifende Überschneidungen für dieselbe
Marktidentität. Frühere Einzelruns, blockierte und fehlgeschlagene Runden bleiben
sichtbar. Dieselbe Rundenidentität zählt im gebundenen Registerstand einmal.
Entwürfe und Warm-up liegen getrennt vor. Historische `UNKNOWN`-Rollen mit
rekonstruierbarem Zeitraum zählen als Performanceüberschneidung. Fehlen belastbare
Grenzen oder die Instrumentidentität, enthält das Ergebnis die ungelöste Runde
separat. Externe Vorkenntnis bleibt `EXTERNAL_KNOWLEDGE_UNKNOWN`.

Ausgeführt mit der lokalen `.venv`:

- `pytest -q tests/unit/test_quant_lab_protocol.py tests/property/test_quant_lab_leakage.py`
  · **31 bestanden** auf dem finalen Paketstand. S1–S12, unabhängige Literal- und
  Fraction-Orakel, OAS-/Schema-Validierung, Teilfehler, Legacy-Intervalle, Seedgaps
  und echte synthetische Test-/Holdout-Revisionen. Die späteren Revisionen ändern
  Equity und den gemeinsamen Manifesthash. Validationlink- und Selectionhashes
  bleiben gleich.
- Breitere Regression · **701 bestanden** mit den neuen Tests, QSL-Modellen,
  Grid-, Strategie- und Signaltests, Bestandsmetriken, Simulator, Legacy-/Strict-
  und Retrospective-Datasets sowie QSL-Contractbeispielen. Anschließend bestanden
  die 31 gezielten Checks erneut nach der Ergänzung für Legacy-Intervalle außerhalb
  des aktuellen Phasenrasters und detailliertere Datengategründe.
- Ruff `check` und `format` für die drei Python-Dateien bestanden.
- Ein unabhängiger Review bestätigte die zeitliche Isolation, die Auswahlregeln,
  die Fehlerbarriere und den kanonischen SQL-Preimage. Alle 31 gezielten Tests
  bestanden auch im Review und bei der Integrationsprüfung.

QSL-03 verantwortet die atomare SQL-Bindung und den Vorgängerregisterstand.
QSL-06 liefert die exakten Analyticsprojektionen. QSL-07 verbindet sie mit dem
Worker, prüft die bestehenden Resultatlinks und persistiert alle Entscheidungen vor
OOS. Die hier geprüften reinen Komponenten liefern dafür die fachliche Evidenz.
