# QSL-06 · Kennzahlen und Vergleiche

Nachweis für Task 2.3 / Issue #82, Design D6 und REQ-QCO-001/002/005.
Die Integration der exakten Werte in die gebundene Auswahl deckt den
Analytics-Anteil von REQ-QEX-007 ab.

`analyze_phase(result, calendar, phase)` wertet ein vorhandenes
`SimulationResult` gegen die bestätigten regulären IEX-15Min-Bargrenzen aus.
Die Phasenmetrik enthält Soll- und Istumfang, einzeln begründete Status,
Aktivität, bargewichtete Exposure und die Kostenprojektion. Fills müssen auf
einem zulässigen Bar-Open liegen. Cash und Menge folgen den vorhandenen
Ausführungen. Bruttomarktwert und Equity werden auf acht Geldstellen getrennt
gerundet. Die Endposition bleibt zum letzten Close bewertet.

`PhaseAnalytics.selection_values` reicht exakte Decimal38-Werte an den
Validationselektor aus QSL-05 weiter. `PhaseAnalytics.to_wire()` rundet die
Anzeige auf zwölf Dezimalstellen beziehungsweise auf acht Geldstellen.
Vollständige Randsitzungen zählen separat. Volatilität und Sharpe erfordern
mindestens 20 vollständige Handelssitzungen und exakt an den Sitzungsgrenzen
liegende Phasen. Eine Early-close-Sitzung zählt als eine Sitzung. Bei fehlender
Flat-Referenz können belegte Aktivitätswarnungen sichtbar sein, während der
Warnstatus die fehlende Vergleichsevidenz ausweist.

`BoundPhaseAnalytics` bindet die Phase, Kalenderbars, Konfiguration, Startcash
und den Bewertungsmodus an das Manifest. `compare_phase` nutzt gebundene
Flat- und Buy-and-hold-Referenzen derselben Phase. Die Degradation verwendet
die Validation des eingefrorenen Testkandidaten desselben Folds. Für die
Degradation braucht die Validation sowohl ihre exakten Auswahlwerte als
auch den im Foldselektor gebundenen Validation-Resultlink-Hash. QSL-07
liefert diesen als `BoundPhaseAnalytics.validation_input_hash`. Der
Vergleichsfingerprint trennt Bindungsdimensionen einschließlich der gesamten
Auswahlpolicy. `compare_batches` gruppiert persistente Reportreferenzen und
liefert paarweise Gründe bei abweichenden Grundlagen oder Teilfehlern.

`validation_sensitivity` projiziert das deklarierte Validationraster und
übernimmt Ränge aus der eingefrorenen Selection. `build_fold_overview`
liefert deskriptive Verteilungen der vollständig ausgewerteten ausgewählten
Testphasen. `build_fold_activity_ranges` liefert die getrennten
Aktivitätsverteilungen serverseitig, da der geschlossene FoldOverview-DTO die
Return-, Drawdown- und Exposure-Verteilungen enthält. `build_comparison_report`
bildet das geschlossene Wire-Modell aus gebundenen Selections, Phasen und dem
vollständigen unveränderlichen ResearchHistory-Stand. Spätere
`currentWarnings`, operative Report- und Batch-IDs, `createdAt` und
`frozenAt` liegen außerhalb des Reporthashs. Die Finalentscheidung wird
gegen das deklarierte Kandidaten- und Foldraster, die exakten Validationzellen
und die Inputhashes der eingefrorenen Foldentscheidungen geprüft. QSL-07 übernimmt
Persistenz, historisch gebundene Kalenderbeschaffung und Routenprojektion.

## Prüfevidenz

- Fünf-Bar-Handrechnung mit geschlossenem und offenem Bestand. Die mittlere
  Bruttoexposure im geschlossenen Fall folgt aus dem unabhängigen Bruch 37/190.
  Cash- und nicht ausführbare LONG-Fälle bestätigen echte Nullaktivität.
- Die 8-stellige HALF_EVEN-Cashgrenze von 0.00000001 verwendet getrennte
  Geldrundungen für Brutto und Equity. Fehlbar, Duplikat, falsche Zeitbindung,
  unbekannte Evidenz, Teilverkauf und nicht endliche Zahlen haben geprüfte
  Fehlerstatus. Null-Equity hat erklärte nicht definierte Exposure/Tageswerte.
- Das Tagesorakel multipliziert rationale Returns und prüft die unabhängige
  Stichprobenvarianz 1/3800. Die Fixtures prüfen 19/20 Sitzungen, Nullvarianz,
  Early-close, DST, partielle Ränder sowie 20 gegen 21 Sitzungen für
  Validation/OOS-Degradation.
- Ein realer Simulatorlauf stimmt für Return, Drawdown, Turnover und Kosten
  mit der unabhängigen Legacy-Regressionsfixture überein. Aktivitätswarnungen
  sind vor Darstellungsrundung an den Grenzen geprüft, einschließlich 1 %
  Baranteil bei 1000 % Nettorendite in einer synthetischen Fixture.
- Zwei Drawdowns mit gleicher Anzeige liegen auf unterschiedlichen Seiten der
  Policygrenze. Der exakte Anteil 2/3 scheitert am zwölfstelligen Minimum
  0.666666666667. Die Auswahlprüfung nutzt jeweils die tatsächlichen
  Ledgerwerte. Vollständige Nichtwahl, fehlende Validationzellen, separate
  Holdout-Bindungen und Vergleichsgruppen sind abgedeckt.
- Die erzeugten Performance-, Selection- und Vergleichsprojektionen werden
  gegen die geschlossenen Vertragskomponenten geprüft. Die gezielte Suite
  mit QSL-05 und beiden Legacy-Regressionen meldete 86 bestandene Tests.
  Negativfälle decken abweichende Degradation-Links und -Werte, fremde
  Finalkandidaten, zusätzliche Finalfolds und widersprüchliche
  Validation-Evidenz ab. Der Reporthash ist bei veränderten operativen
  IDs und Zeitstempeln stabil und reagiert auf Änderungen der Registerbindung.
  Ruff meldete für die vier geänderten Python-Dateien keine Befunde.
- Der unabhängige Nachreview bestätigte alle drei Korrekturen mit 39 gezielten
  Tests. Der Integrationslauf mit den Leakage-Tests und den beiden
  Legacy-Regressionen bestand mit 91 Tests.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_quant_lab_metrics.py tests/unit/test_quant_lab_comparison.py tests/unit/test_quant_lab_protocol.py tests/unit/test_backtest_metrics.py tests/unit/test_retrospective_simulator.py
.venv/Scripts/ruff.exe check packages/backtesting/experiment_metrics.py packages/backtesting/comparison.py tests/unit/test_quant_lab_metrics.py tests/unit/test_quant_lab_comparison.py
```
