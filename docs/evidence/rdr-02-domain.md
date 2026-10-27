# RDR-02 – Gemeinsame konservative Datenprüfung

Stand: 2026-10-27. Nachweis für die reine Domain; keine Provideranfrage,
Handelsaktivierung oder Order. Reale Nutzungsrechte, historische externe
Publikation und tatsächliche Providerlatenzen bleiben unverifiziert.

## Implementierung und Schnittstellen

`packages.backtesting.readiness.assess(config, frozen, calendar_rows,
quality_rows=(), coverage_proofs=(), *, trust=None, observations=(), budget=None,
checkpoint=..., clock=...) -> AssessmentResult` verarbeitet ausschließlich
unveränderliche, begrenzte Eingangstupel. `config` ist `ExperimentConfig`, `frozen`
ist `FrozenDataset`. Die Diagnose startet weder Materialisierung noch Simulation.

`AssessmentResult` trennt `status`, `eligibility`, `evaluation_complete` und
`publication_complete`. `findings` sind stabil sortierte, deterministisch
identifizierte `Finding`-Werte; `windows` sind typisierte `Window`-Werte;
`metrics` sind rekursiv unveränderliche Projektionen. `report_dict(assessment_id,
created_at=None)` erzeugt den geschlossenen kanonischen `ReadinessReport` samt
Hash. `Finding.to_dict()` erzeugt `ReadinessFinding`. Unvollständige Ergebnisse
können keinen Bericht erzeugen. Die HTTP-Persistenz übernimmt RDR-05.

`TrustSnapshot.from_mapping(...)` übernimmt die versionierte RDR-03-Projektion
mit `version`, `policy_version` und `records`. Jeder `EvidenceTrust` bindet
`reference`, `kind`, fünf unabhängige `dimensions`, `revoked`, tatsächliches
`ingested_at` und `commit_version`. `TrustSnapshot.gate(kind, reference,
knowledge_at)` ist derselbe aktuelle Prüfport für Diagnose und
`materialize_fold(..., trust_check=...)`. Aktueller Widerruf wird auch bei einem
alten Datensnapshot berücksichtigt. RDR-05 muss diesen Port in sämtlichen
tatsächlichen Backtestpfaden anbinden; alte Berichte bleiben unverändert.

`CoverageResult` und `QualityResult` erklären unbekannte, negative,
widersprüchliche, ungeprüfte und widerrufene Belege. Die vorhandenen
`covering_proof`-/`quality_at`-Wrapper behalten ihre Optional-/Bool-Schnittstellen.
`plan_fold`, `input_candidates` und `outcome_candidates` werden von Diagnose und
Materialisierer gemeinsam verwendet. Bestehende interne `BLOCKED_*`-Codes und
Datenwahl bleiben erhalten. Öffentliche Befunde verwenden die vorhandenen
HTTP-Reasoncodes und zusätzlich die präzise Kategorie; die interne
`Finding.backtest_reason` bewahrt den ursprünglichen Materialisierercode.

## Präzise Vertrauensaussagen

Bestehende lokale Beobachtungen erhalten getrennte, eng begrenzte Aussagen:

| Dimension | Verfahren | Ausschließlich belegte Aussage |
| --- | --- | --- |
| Integrität | `NORMALIZED_CONTENT` | Normalisierter lokaler Inhalt und gebundener Snapshot |
| Herkunft | `DB_INGESTION_PROVENANCE` | Bestehender kontrollierter lokaler Quellenport |
| Zeitliche Authentizität | `DB_COMMITTED_VISIBILITY` | Tatsächliche lokale Sichtbarkeit, ausdrücklich keine Providerpublikation |
| Vollständigkeit | `SHARED_CALENDAR_IDENTITIES` | Nach sichtbarem Kalender erwartete lokale Identitäten |
| Fachliche Qualität | `SHARED_OHLC_QUALITY` | Gemeinsame lokale OHLC-/Qualitätsregeln |

Diese Status gelten nur für die jeweilige benannte lokale Aussage. Sie beweisen
keine unabhängige externe Herkunftssignatur, historische öffentliche
Verfügbarkeit oder Lizenz. Referenzen binden die lokale Aussage an den
unveränderlichen Datensnapshot. Fehlende Empfangs-/Commitmessungen werden nicht
aus Ingestion rekonstruiert und verbessern keine Messquote.

Importierte externe Evidenz wird niemals durch lokale Speicherung verifiziert.
Die normalisierten Reader verwenden deren gespeicherte unabhängige
Vertrauensresultate und aktuelle Widerrufe. Fehlender unabhängiger Zeitbeweis
bleibt `UNKNOWN/TIME_UNPROVEN`. Ungeprüfte Pflichtdimensionen ergeben
`NOT_EVALUATED`; bekannte Ablehnung oder Widerruf sperren. Das gilt intrinsisch
auch dann, wenn ein alter Aufrufer keinen zusätzlichen Trustcallback übergibt.
Vor Installation von RDR-03 prüfen die Reader die Existenz der additiven Tabelle
und laden das neue Speichermodul nicht. RDR-02 bleibt deshalb ohne Migration
0030 installierbar. Neue SQLreads oder reale DB-/Rechteabnahme werden hier nicht
als bereits durch synthetische Readermodelle bewiesen ausgegeben.

## Regeln und Begrenzung

- Signalwissen bleibt Barschluss +60s, Outcome bleibt +300s, jeweils inklusive
  Gleichheit. Verfügbarkeit und tatsächliche Ingestion sowie Snapshotversion
  bleiben gemeinsam verbindlich. Outcomezugriff erweitert kein Signalwissen.
- Warm-up wird am jeweiligen Signalwissenszeitpunkt und im registrierten
  rollenden Fenster geprüft. Ein fehlender erster Testbalken verschiebt keinen
  Foldstart. Kapitalmaßnahmenbelege müssen bereits `warm_start` bis zum gesamten
  `fold.test_end` abdecken.
- Unbekannte Kalender erzeugen keine erfundenen fehlenden Baridentitäten.
  Unabhängige bekannte Teilbereiche werden weiter diagnostiziert. Bekannte
  negative, widersprüchliche oder widerrufene Coverage bleibt auch neben einem
  unbekannten Kalender `BLOCKED`.
- Tatsächlich maximale Bar-, Strict-PIT- und vollständige Experimentfenster sind
  getrennt. Unbekannte Kalenderränder unterbrechen Läufe; belegte handelsfreie
  Zeiten erzeugen keine Sollbars. Kalender bestimmt DST, Feiertage und Early
  Close. Vollständige Protokollfenster benötigen beide Sichten.
- Fristmetriken deduplizieren Baridentitäten. Empfang, Ingestion und bestätigte
  Sichtbarkeit sind getrennt; Bestätigung ist eine obere Schranke nach Commit.
  Unbekannte Bestätigung bleibt eine unbekannte Messung und verändert die
  bestehende Materialisiererregel nicht. Quantile verwenden exakte
  Decimal-Differenzen und Nearest Rank. Unbekannte/negative/nicht vergleichbare
  Zeitpaare werden separat gezählt und ausgeschlossen.
- Höchstens 100000 Zeilen je Eingangsbereich, 10000 Befunde, 12MiB Veröffentlichung,
  300 Sekunden und 200 Fenster je öffentlicher Fensterliste. Referenzlisten sind
  ebenfalls auf 200 begrenzt. Überlauf liefert ausschließlich
  `FAILED/BUDGET_EXHAUSTED`, `UNKNOWN` und keinen publizierbaren Teilbericht.
  Queue, Attempts, gemeinsame Workerlease und atomare SQL-Publikation folgen in
  RDR-05.

## Prüfevidenz

Ausgeführt mit dem gepinnten Projektinterpreter `.venv/Scripts/python.exe`:

```text
pytest tests/unit/test_readiness_assessment.py tests/unit/test_backtest_dataset.py -q
69 passed

pytest tests/unit/test_backtest_version.py tests/unit/test_backtest_strategies.py
  tests/unit/test_backtest_storage.py tests/unit/test_backtest_simulator.py
  tests/unit/test_backtest_request.py tests/unit/test_backtest_models.py
  tests/unit/test_backtest_metrics.py tests/unit/test_backtest_evaluation.py
  tests/unit/test_backtest_dataset.py tests/unit/test_readiness_assessment.py
  --basetemp=.tooling/rdr/pytest-rdr02-regression-20261027 -q
114 passed

ruff check packages/backtesting/readiness.py packages/backtesting/dataset.py
  packages/backtesting/coverage.py packages/backtesting/calendar.py
  tests/unit/test_readiness_assessment.py tests/unit/test_backtest_dataset.py
All checks passed!
```

55 neue Assessorfälle prüfen unter anderem Parität und sämtliche vier
Zulässigkeitszustände, mehrere unabhängige Blocker, Signatur ohne Zeitbeweis,
aktuellen Widerruf, unbekannte Kompromittierungszeit als sperrenden Truststand,
vollständige/partielle Qualitätsauflösung, genaue Fristen und +1µs,
Verfügbarkeits-/Ingestiondifferenzen, Snapshotgrenzen, Warm-up,
`AMBIGUOUS_DATA`/`CALENDAR_CHANGED`, früh/verspätet bekannte Kalender,
DST/Thanksgiving/Early Close, maximale unterschiedliche Fristfenster,
6/10- und 8/10-Quoten, fehlende Bars, Duplikate, A→B→A, unbekannte/späte
Commitbestätigung, Nearest Rank, negative Uhrenpaare, sämtliche Domainbudgets,
Fenceverlust, stabile IDs/Sortierung und kanonische HTTP-Projektionen.

Zuordnung: REQ-RDR-001/002/004/007/008, REQ-HMD-009/010;
T-RDR-001/002/003/004/007/008/013/014/015/016/017 und
T-HMD-019/020/021/022/023/024/025. Runtime-, PostgreSQL-, Rollen- und
Transportabnahme bleiben den jeweils eigenen nachfolgenden Paketen und RDR-08
zugeordnet. Keine Aussage über echte Providerrechte oder Onlineeignung.

## Integrationsabnahme durch den Hauptagenten

Die 114 Backtesting-/Assessor-Unitprüfungen wurden wiederholt bestanden; zusätzlich
bestand die bestehende echte PostgreSQL-Datasetregression in einer isolierten
Testdatenbank (39.87s). Der unabhängige Reviewer bestätigte den behobenen
Kalender-/Coverage-Aggregationsbefund und meldete keine offenen P1/P2.
Ruff bestanden. Der RDR-02-Commit enthält ausschließlich die sechs eigenen
Domain-/Test-/Evidenzdateien sowie Runbook, Taskstatus und Issue-Syncmapping;
parallele Persistenz-, Import- und Sammlungsarbeiten bleiben außerhalb dieses Commits.
