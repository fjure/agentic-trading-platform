# Implementierungsverifikation · Quant Strategy Lab v1

OpenSpec-Change `quant-strategy-lab-v1`, Workflow `spec-driven`. Die drei
Delta-Spezifikationen enthalten 16 Anforderungen und 21 Szenarien. Die zwölf
Auswahlorakel sind zusätzliche Prüfdetails aus der Auswahlpolicy. Die strikte
OpenSpec-Validierung ist bestanden. Der Planungsstatus bestätigt die
Vollständigkeit der Planungsartefakte. Die Laufzeitnachweise sind separat
aufgeführt.

## Anforderungs- und Szenarioabdeckung

Die Paketnachweise dokumentieren jeweils tatsächlich ausgeführte Prüfungen.
Die Schlussabnahme ergänzt die belastete, zusammenhängende Laufzeitprüfung.

| Anforderung | Szenario | Implementierung und Prüfnachweis |
| --- | --- | --- |
| REQ-QSR-001 | T-QSR-001 Versionsänderung | `strategies.py` prüft Version und Implementierungsfingerprint. `test_quant_lab_models.py` und `test_quant_lab_strategies.py` prüfen geänderte Quellen, den Target-Enum und eingefrorene Legacy-Hashes. [QSL-02](qsl-02.md), [QSL-04](qsl-04.md). |
| REQ-QSR-001 | T-QSR-002 Geschlossene Eingabe | Der Registry-Resolver, das geschlossene OpenAPI-Schema und 0035 verwerfen unbekannte oder nicht gebundene Strategien. Typ-, Parameter- und Versionsnegative in `test_quant_lab_grid.py`, `test_quant_lab_examples.py` und `test_quant_lab_storage.py`. [QSL-01](qsl-01.md), [QSL-03](qsl-03.md). |
| REQ-QSR-002 | T-QSR-003 EMA-Referenz und Zukunft | `indicators.py` berechnet die EMA im bestehenden Signal- und Simulationspfad. Rationale Gewichtsoracle sowie Outcome- und Zukunftsperturbation in `test_quant_lab_signals.py`. [QSL-04](qsl-04.md). |
| REQ-QSR-002 | T-QSR-004 RSI-Randfälle | Wilder-Glättung und inklusive Eintritts-/Austrittsschwellen in `indicators.py`. Handorakel, Nullfall, monotone Folgen und Hysterese in `test_quant_lab_strategies.py` und `test_quant_lab_signals.py`. [QSL-04](qsl-04.md). |
| REQ-QSR-002 | T-QSR-005 Gemeinsamer Warm-up | `experiment.py`, `dataset.py`, `retrospective.py` und `simulator.py` binden denselben Seed je Phase, einschließlich Referenzen. Fehlende Bars, spätere Korrekturen und unterbundene Seed-Fills werden in beiden Modi geprüft. [QSL-02](qsl-02.md), [QSL-04](qsl-04.md). |
| REQ-QEX-001 | T-QEX-001 Enumeration und ungültiges Grid | `experiment.py` prüft das ganze Raster vor der Kandidatenerzeugung. `test_quant_lab_grid.py` prüft die neun festen IDs, die 32/33-Grenze und frühes Verwerfen großer Produkte. Der echte API- und Browserpfad bestätigt 9+2. [QSL-02](qsl-02.md), [QSL-07](qsl-07.md), [QSL-08](qsl-08.md). |
| REQ-QEX-002 | T-QEX-002 Korrektur zwischen Kandidaten | Aufnahmeversion, Revisionen und Seeds werden in `quant_lab_data.py` und `ExperimentStorage.bind_snapshot` gebunden. PostgreSQL-Guard, falsche Revision, Restore und Wiederanlauf prüfen die persistierte Grundlage. [QSL-03](qsl-03.md), [QSL-07](qsl-07.md). |
| REQ-QEX-003 | T-QEX-003 Verbotenes Leakage | `experiment_protocol.py` akzeptiert typisierte Validationeingaben. `QuantLabWorker._process` speichert alle Selektionen vor dem ersten OOS-Aufruf. Propertytests und eine echte spätere Testkorrektur prüfen unveränderte Auswahlidentität. [QSL-05](qsl-05.md), [QSL-07](qsl-07.md). |
| REQ-QEX-003 | T-QEX-004 Holdout und Wiederverwendung | Der eingefrorene Plan gibt nur dem gewählten Kandidaten und den Referenzen OOS-/Holdout-Arbeit. `quant_lab_history.py` projiziert registrierte Exposition und frühere Runden, `comparison.py` bindet Sensitivität an Validation. [QSL-05](qsl-05.md), [QSL-06](qsl-06.md), [QSL-07](qsl-07.md). |
| REQ-QEX-004 | T-QEX-005 Überlast vor Arbeit | 0035 bündelt gewichtete Admission und Lease, `estimate_budget` begrenzt alle tatsächlichen Aufrufe einschließlich Seeds und Referenzen. Storage- und API-Harness prüfen volle Queue und Ablehnung. Der belastete Containerlauf gehört zur Schlussabnahme. [QSL-03](qsl-03.md), [QSL-07](qsl-07.md). |
| REQ-QEX-005 | T-QEX-006 Crash und Altworker | `experiment_storage.py` und 0035/0036 erzwingen Fence, Fortschritt und vollständige Veröffentlichung. `test_quant_lab_jobs.py` und `test_quant_lab_execution.py` prüfen alte Claim-SQL, stale Fence und Abbrüche nach Auswahl-, Child- und Report-Commit. [QSL-03](qsl-03.md), [QSL-07](qsl-07.md). |
| REQ-QEX-006 | T-QEX-007 Quality und finanzielle Isolation | Die Datenports trennen Strict PIT und Retrospective Exploratory. SQL-Gates blockieren aktuelle negative Evidenz, und die separaten Workerrollen haben keine Finanzrechte. PostgreSQL, Browser und Legacy-Regression prüfen Modusgrenzen. Safety unter Last gehört zur Schlussabnahme. [QSL-03](qsl-03.md), [QSL-07](qsl-07.md), [QSL-08](qsl-08.md). |
| REQ-QEX-007 | T-QEX-008 Rendite und Kapitalerhaltung | `experiment_protocol.py` verwendet beide geschlossenen Policies mit exakten Werten. `test_quant_lab_protocol.py` und das unabhängige Fraction-Orakel prüfen den Wechsel des Gewinners. [QSL-05](qsl-05.md). |
| REQ-QEX-007 | T-QEX-009 Nichtwahl, Grenzen und unveränderliche Identität | Policyparameter und Limits gehen in die kanonische Bindung ein. `test_quant_lab_protocol.py` prüft Gleichheit, Decimal38 und Nichtwahl. Worker, SQL und Browser prüfen die gespeicherte Nichtwahl. [QSL-05](qsl-05.md), [QSL-07](qsl-07.md), [QSL-08](qsl-08.md). |
| REQ-QEX-008 | T-QEX-010 Fehlende Phase und schlechter Holdout | `experiment.py` prüft das komplette Protokoll, `experiment_protocol.py` bindet die Forschungsrollen und `NOT_TRAINABLE`. Wiederanlauf und spätere Outcome-Korrekturen verändern die eingefrorene Auswahl nicht. [QSL-02](qsl-02.md), [QSL-05](qsl-05.md), [QSL-07](qsl-07.md). |
| REQ-QEX-009 | T-QEX-011 Neue Hypothese auf bekannter Periode | 0035 registriert alle Phasen und den Vorgängerstand unter gemeinsamer Barriere. `quant_lab_history.py` trennt frühere Nutzung, tatsächliche Statuszahlen und unbekannte externe Vorkenntnis. Historischer Upgrade- und API-Nachweis plus sichtbare Browserprojektion. [QSL-03](qsl-03.md), [QSL-07](qsl-07.md), [QSL-08](qsl-08.md). |
| REQ-QCO-001 | T-QCO-001 Abweichende Basis oder Teilfehler | `comparison.py` bindet alle Vergleichsdimensionen und weist inkompatible Gruppen und Teilfehler aus. `test_quant_lab_comparison.py` prüft andere Snapshots, Policies, Kosten, Builds und fehlende Resultate. [QSL-06](qsl-06.md), [QSL-07](qsl-07.md). |
| REQ-QCO-002 | T-QCO-002 Numerische Randfälle | `experiment_metrics.py` bindet die Tagesreihe an den Kalender. Tests prüfen 19/20 Sitzungen, Early Close, DST, Nullvarianz, Nullnenner und getrennte Foldstatistik. [QSL-06](qsl-06.md). |
| REQ-QCO-003 | T-QCO-003 Vertrag und Angriff | 15 reale Operationen über `quant_lab_routes.py`, Control-API und privaten Relay. Die Vertrags- und Integrationstests prüfen Auth, Origin, CSRF, Rollen- und Sitzungscursor, Schema, Fehler und Legacy-Pfade. Generator und Breaking-Check wurden tatsächlich ausgeführt. [QSL-01](qsl-01.md), [QSL-07](qsl-07.md). |
| REQ-QCO-004 | T-QCO-004 Echter Reload und Sessionverlust | `apps/dashboard/src/app/research/quant-lab/` nutzt Angular Material und generierten Client. Echter Chromium-Workflow prüft Raster, Kurven, Pagination, persistente Links und verspätete Pollantwort nach Sessionverlust. [QSL-08](qsl-08.md). |
| REQ-QCO-005 | T-QCO-005 Tatsächliche Aktivität, offene Position und leere Cashphase | `experiment_metrics.py` leitet Aktivität und Exposure aus dem Ledger ab. Fünf-Bar-Orakel, offene Position, Cashphase, Gebühren, Schwellen und serverseitige Anzeige sind geprüft. [QSL-06](qsl-06.md), [QSL-08](qsl-08.md). |

## Numerische Auswahlorakel

Die Tests liegen in `tests/unit/test_quant_lab_protocol.py` und
`tests/property/test_quant_lab_leakage.py`. Die ergänzenden Metrikorakel stehen
in `test_quant_lab_metrics.py` und `test_quant_lab_comparison.py`.

| Orakel | Geprüfter Fall |
| --- | --- |
| S1 | Bei gleicher zulässiger Kandidatenmenge bevorzugt die Standardpolicy den höheren Return und die Kapitalerhaltung den geringeren Drawdown. |
| S2 | Ein expliziter Flat-Suchkandidat darf gewinnen. Die feste Flat-Referenz behält ihre eigene Rolle. |
| S3 | Negative Nettorenditen bleiben negativ und werden nach derselben Regel verglichen. |
| S4 | Die Drawdowngrenze ist inklusiv. Ein Wert oberhalb der Grenze scheitert auch bei gleicher Anzeige. |
| S5 | Vollständige Grenzverletzung ergibt Nichtwahl und lässt nur die Referenzarbeit zu. |
| S6 | Sharpe-N/A allein verhindert keine Wahl. Fehlende Pflichtwerte sperren den Selektor. |
| S7 | Return, Drawdown und Turnover binden den Tie-Breaker an die kleinste kanonische Kandidaten-ID. |
| S8 | Jede Änderung von Policy-ID, Grenzwert oder optionalem Minimum verändert die fachliche Identität. |
| S9 | Veränderte Test- und Holdout-Outcomes lassen Validation-Link und Auswahlhash stabil. Späte Phasen können nicht als Eingabe übergeben werden. |
| S10 | Final zulässig ist nur, wer alle Gates in jeder Validation erfüllt. Mediane werden aus den gebundenen Komponenten berechnet. |
| S11 | 19/20 Sitzungen, explizite Einstiegsgrenze und ein Decimal38-Schritt unter dem Aktivitätsminimum werden unterschieden. |
| S12 | Ein echter Teilfehler blockiert Ranking und OOS. Vollständige Grenzverletzung aller Kandidaten ist dokumentierte Nichtwahl. |

## Designkohärenz

| Entscheidung | Abgleich |
| --- | --- |
| D1 | Geschlossenes, versioniertes Strategieregister und vorhandener Ledger-/Fill-Kern. Registry und SQL weisen Versions- und Fingerprint-Drift ab. |
| D2 | EMA und Wilder-RSI verwenden feste Decimal-Methodik, kausale Seeds und frischen Zustand je Phase. |
| D3 | Phasenrollen, reine Validationwahl, dauerhafter gemeinsamer Freeze und selektiver OOS-/Holdout-Zugriff. |
| D4 | Unveränderliche Versionen, gemeinsame Aufnahmebindung, fachliche Hashes und physische FK-/Publikationsbindung. 0035 und 0036 sind additive Migrationen. |
| D5 | Gewichtete gemeinsame Queue, eine aktive Lease, Fence, 295-Sekunden-Frist, drei Versuche und getrennte Zeilen-, Barschritt- und Publikationsgrenzen. Belastungsnachweis separat. |
| D6 | Servermetriken aus dem Ledger und Kalender, gebundene Vergleichsgruppen, separate Fold- und Aktivitätsverteilungen. |
| D7 | Additiver Vertrag, Authentifizierung, generierter Client, Angular Material und rollenfeste Kurven. |

## Unabhängiger Quant- und Leakage-Abgleich

Der Worker übergibt dem Selektor ausschließlich `ValidationSelectionInput`
aus dem Validationpfad. Die Auswahl referenziert den phasenlokalen Evidenzhash
mit Seed-, Outcome- und Entscheidungsbindung. Die echte Reliability-Referenz
bleibt separat an die publizierte Zeile gebunden. Der PostgreSQL-Trigger
prüft das Evidenzmaterial und erzwingt den vollständigen Auswahlstand vor
einem Test- oder Holdout-Link. Beim Neustart werden gebundene Links und der
gespeicherte Freezezeitpunkt wiederverwendet. Die echte spätere Testkorrektur
im Worker-Harness und das separate Fraction-Orakel prüfen unterschiedliche
Ebenen derselben Eigenschaft.

## Stand der Schlussabnahme

Der Chromium-Lauf mit 26 Bars je Sitzung prüft den normativen
Strict-PIT-Nichtwahlzweig einschließlich 9.178 persistierter Barschritte.
Der serverseitige Retrospective-Lauf prüft den Auswahlzweig einschließlich
negativem Holdout. Die gemessenen Laufzeiten, Grenz- und Safety-Nachweise
sowie der abschließende Issue-Abgleich werden in [QSL-09](qsl-09.md) geführt.
