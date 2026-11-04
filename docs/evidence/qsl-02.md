# QSL-02 – Register und Experimentprimitive

Stand: 2026-11-04. Paketumfang ist Task 1.2 / Issue #78 des freigegebenen
Changes `quant-strategy-lab-v1`. Dies ist Evidenz für reine Domänenkomponenten,
nicht für einen verfügbaren HTTP-/Workerworkflow oder Handelsfreigaben.

## Implementierter Umfang

- Die Legacy-Registry bleibt bei flat/buy-hold/SMA. `QUANT_REGISTRY` enthält
  zusätzlich EMA/RSI mit geschlossenen Parametern, Markt-/Inputmetadaten,
  Forschungsmodi, Warm-up-/Signalversion, Einschränkungen und NOT_TRAINABLE.
  Der Resolver prüft konkrete Metadatendeklarationen und normalisierte relevante
  Quellartefakte gegen feste Pins; falsche Versionen/Fingerprints werden abgewiesen.
- EMA/RSI sind in diesem Paket ausschließlich Definitionen und Parameter-/Seedports.
  `resolve_strategy(..., require_execution=True)` verweigert beide mit
  VERSION_UNAVAILABLE. Ihre Signalmaschinen und endgültigen ausführbaren
  Artefaktbindungen gehören zu QSL-04; vor der ersten QSL-Persistenz/Ausführung
  müssen diese vollständig gebunden sein. Es existiert kein ausführbarer Ersatz.
- Unveränderliche Selectionpolicy, endliche SearchSpaces, Kandidaten, Phasenrollen,
  Definitionsrevisionen und `quant-experiment-v1`-Manifeste sind additiv modelliert.
  Alle Policyparameter einschließlich null und Methodenfingerprint gehen in die
  neue Policy-/Definitions-/Manifest-/Selectionidentität ein. Die vorhandenen
  Datenselektionspolicies und ihre Hashpräimages bleiben getrennt.
- Das Gesamtprodukt wird vor Kandidatenmaterialisierung saturierend auf 33
  begrenzt und bei mehr als 32 abgewiesen. Unbekannte Parameter, boolesche Perioden,
  kanonische Duplikate und jede ungültige Kombination verwerfen die gesamte Suche.
  Kandidatenidentität ist SHA256 der kanonischen Serialisierung von
  `(strategy_id, "1", parameter_pairs)`. JSON-kompatible Policy-/Definitionspräimages
  sind für den gesonderten SQLport verfügbar.
- Gemeinsames W wird aus den registrierten SMA-/EMA-/RSIregeln bestimmt. Alle
  Phasen lesen dieselbe bereits eingefrorene Revisionsmenge. Pro Phase enthält der
  erste Input exakt W Seedbars plus Signalbar, jeder weitere exakt eine Signalbar.
  Kein wachsendes Präfixtuple und keine erneute Seedzufuhr. Der tatsächliche Seedstart
  begrenzt die inklusive 366-Tage-Spanne; ein Legacy-Folddummy zählt bei W=0 nicht.
- `from_phase_datasets` bindet echte Revisionen/Cutoffs und Metadaten der Phasenports.
  Strict verlangt Kalender-, tatsächliche Qualityhash- und Coveragebindung; auch der
  bekannte leere Qualityzustand besitzt im Bestand einen Hash. Retrospective darf
  leere Quality-/Coveragebezüge unter expliziter Snapshot-Evidencepolicy binden und
  behauptet damit keine historischen Strict-Coveragebelege. Instrument/Markt,
  Snapshot, Seedprefix, vollständige Strict-Steps/Outcomes und kausale Cutoffs werden
  vor der Manifestbindung geprüft.

`models.py` benötigt keine Änderung. Legacy-`ExperimentConfig`, `Manifest` und
Retrospective-Hashpräimages bleiben erhalten. Keine SQL-, HTTP-, Queue-, Simulator-
oder Brokerverdrahtung gehört zu diesem Paket.

## Tatsächliche Prüfungen

Alle Kommandos liefen mit der vorhandenen `.venv`, ohne Paketinstallation.

```powershell
./.venv/Scripts/python.exe -m ruff check packages/backtesting/strategies.py packages/backtesting/models.py packages/backtesting/experiment.py packages/backtesting/dataset.py packages/backtesting/retrospective.py
```

Ergebnis: `All checks passed!`.

```powershell
$qslFinalUnitFiles = @(rg --files tests/unit | Where-Object { $_ -match '(test_backtest|test_retrospective).*\.py$' })
& ./.venv/Scripts/python.exe -m pytest tests/unit/test_quant_lab_models.py tests/unit/test_quant_lab_grid.py @qslFinalUnitFiles -q --basetemp=.tooling/output/qsl02-domain-combined-20261104
```

Ergebnis vor Ergänzung der abschließenden Reviewregressionen: **268 passed in
13.23s**. Darin sämtliche 153 bestehenden Backtest-/Retrospective-Unittests und 115
neue QSLtests; Literalorakel für Legacy-Config/Manifest/Simulation/Evaluation sowie
Retrospective-Bindungen blieben unverändert. Neue Orakel prüfen insbesondere das
3×3-Grid mit unabhängig berechneten neun SHA256-IDs, Grenzen/Typen/Duplikate,
Policy-/Selectionhashes, Forschungsrollen, gemeinsame Seeds beider Modi,
Revisionen-/Zukunftsperturbation, echte Phasen→Manifestbindung und 366-Tage-Ränder.

Unabhängiger Review reproduzierte und meldete drei P2-Grenzfehler: unvollständige
Warm-up-/Metadatenfingerprintbindung, fehlende Instrumentprüfung am Factoryport
und fehlende Strict-Strukturprüfung bei manipulierten Phasen. Alle drei wurden
behoben; abschließende persistente Regressionen/Reviewbestätigung werden ergänzt.

## Abschließende Hauptagentenprüfung

Die drei unabhängigen Reviewbefunde besitzen jetzt elf dauerhafte
Negativregressionen. Zusätzlich wurde die 2000-Zeichen-Grenze der Hypothese auf
die unveränderte Eingabe angewandt; Padding darf die Grenze nicht umgehen.
Vier weitere Grenzfälle belegen genau 2000 sowie mehr als 2000 Zeichen.
Beide Ergänzungen sind unabhängig nachgeprüft: elf beziehungsweise vier Tests
bestanden, keine offenen Reviewbefunde.

Finales tatsächlich ausgeführtes Kommando (projektlokaler temporärer Testpfad,
für lokale Testclients außerhalb der Windows-Socketbeschränkung):

```powershell
$qslDomainRegression = @(rg --files tests/unit | Where-Object { $_ -match '(test_backtest|test_retrospective).*\.py$' })
& .venv/Scripts/python.exe -m pytest tests/unit/test_quant_lab_models.py tests/unit/test_quant_lab_grid.py @qslDomainRegression tests/security/test_backtest_access.py tests/security/test_control_auth.py -q -p no:hypothesispytest --basetemp=.tooling/output/qsl02-root-final-review --tb=short
```

**326 passed, 1 bestehende Starlette-Deprecationwarnung, 15.12s**. Enthalten sind
alle 153 bestehenden Backtest-/Retrospective-Unittests, 130 QSL-Fälle und
43 Auth-/Origin-/Anfragegrenzfälle. Ruff auf allen fünf Domänendateien und beiden
Testdateien, OpenSpec strict sowie diff --check bestanden. OAS und generierter
Client blieben unverändert. Source-Credentialscan: 1427 Dateien, null Befunde;
kein Credentialwert ausgegeben. Die unabhängige 100000/100001-Grenzprobe für
beide Materializer und Manifestmetadaten bestand ebenfalls.

QSL-01-main `3fe4148ba6d7393218b749a322d775c1ecf22019` ist durch erfolgreiche
Contracts-CI 41818148762 bestätigt. Vor QSL-02-Abschluss werden eigener
Issuebranch, Codeintegration, exakte Remote-SHAs und derselbe Issuegraph erneut
geprüft. QSL-03-SQLänderungen im gemeinsamen Arbeitsverzeichnis gehören nicht
zum QSL-02-Commit. Keine zusätzliche SQL-/Container-Safetyabnahme aus diesem
reinen Domänenpaket behauptet.

## Grenzen der zusätzlichen Hilfsversuche

- `uv run pytest ...` startete keine Tests: der in PATH gefundene Chocolatey-
  `python3.13.exe` verweist auf ein fehlendes `c:\python313\python.exe`.
  Derselbe Testumfang lief anschließend mit `.venv/Scripts/python.exe`.
- Ein breiter Unitversuch endete bei einer temporären Verzeichnisfixture mit
  `94 passed, 1 error`; der Sandbox-Tempordner ließ sich nicht anlegen. Der
  vollständige Wiederholungslauf mit projektlokalem `--basetemp` bestand.
- Der zusätzliche Versuch mit sämtlichen Backtest-/Retrospective-Unit- und
  Securitydateien (`rg --files tests/unit tests/security`, gleiches Namensmuster)
  wurde nach vier F-Markern ohne abschließende Failuredetails abgebrochen.
  Node-IDs/Fehlerursachen wurden dabei nicht erfasst und sind ungeklärt; daraus
  folgt keine erfolgreiche Securityprüfung. Ein separater Lauf von
  `tests/security/test_backtest_access.py` wurde nach langem Warten ohne Ergebnis
  ebenfalls abgebrochen. Diese Versuche sind keine Abnahmeevidenz und dürfen
  nicht pauschal einer Sandboxursache zugeschrieben werden.
- PostgreSQL-/Container-/HTTP-/Safetyintegration und vollständige Source-/Contract-
  Abnahme werden durch die dafür zuständigen späteren Pakete/Hauptagenten geprüft.
  Keine Marktdatenverfügbarkeit, statistische Sicherheit oder Handelsfähigkeit
  wird aus diesen synthetischen Unittests abgeleitet.

Taskcheckbox, Issuesynchronisierung, OpenSpec strict und Gitintegration erfolgen
durch den Hauptagenten nach abgeschlossenem Review. Dieser Teilagent hat keine
Gitmutation oder externe Schreiboperation ausgeführt.
