# Apply refinement 2026-11-05

Im freigegebenen Apply wurden zwei konkrete Integrationslücken gefunden.
Anforderungen und neun Issue-IDs bleiben erhalten; keine Abnahme wird gelockert.

## Trusted strategy registry

D1/D4 verlangen die Prüfung registrierter ID/Version/Fingerprint auch an direkten
SQLports. Vom Aufrufer gelieferte, untereinander konsistente JSON-Hashes allein
reichen nicht. Migration 0035 war noch nicht veröffentlicht. Sie installiert
deshalb erst nach unabhängig geprüftem QSL-04 genau einen finalen ausführbaren
Katalog. Control und Worker erhalten kein Registrierungs-/Änderungsrecht.

Die reine QSL-02-Metadatenphase war nicht über die fehlende QSL-API verfügbar;
keine produktiven QSL-Experimente verwendeten die noch nicht ausführbaren
rekursiven Definitionen. Bereits benutzte Implementierungen oder alte
Backtesthashes werden nicht ersetzt. Nach Erstveröffentlichung bleibt jeder
v1-Fingerprint unveränderlich; Änderungen benötigen eine neue Strategieversion.

QSL-03 erhält zusätzlich QSL-04 als Integrationsvoraussetzung. Implementierung
und Tests dürfen bei getrennten Dateien weiter vorbereitet werden. Der Graph
hat weiter neun Knoten und nun 19 Kanten, ist azyklisch und verwendet dieselben
Issues #77–#85. QSL-04 hängt weiterhin nur von QSL-02 ab; kein Kreis entsteht.
Der Syncvalidator prüft deshalb echte Topologie statt Reihenfolge der
Tasküberschriften; fehlende Ziele, doppelte Kanten und Zyklen bleiben Fehler.

## Reliability projection without widening legacy unions

Der kanonische BacktestReliabilityReportV1 bindet RETROSPECTIVE_EXPLORATORY,
retrospective-v1 und ein altes Assessment. Auch die alte Runprojektion besitzt
eine geschlossene Drei-Strategienunion. Die frühere D7-Verweisung auf diese
unveränderten Antworten kann QSL-Strict und neue EMA-/RSI-Runs nicht darstellen.

Der bestehende Reportstore wird um eine getrennte QSL-FINAL-Variante erweitert:
exklusiver Parentbatch-/Childrun-/Phasen-/Snapshotbezug statt vorgetäuschtem
Legacyassessment. Bestehende Legacyzeilen und ihre unveränderten Verträge
bleiben erhalten. Keine zusätzliche Queue, kein zweiter Reportstore und kein
zweiter Simulator. Interne Reportarbeit und Publikationsbytes zählen zum
bereits reservierten Batchbudget.

Ein additiver Batch-gebundener GET liefert die geschlossene QSL-Projektion:
`/api/v1/research/experiment-batches/{id}/reliability-reports/{reportId}`.
getQuantLabReliabilityReport bleibt wie alle übrigen neuen Routen bis QSL-07
ausdrücklich nicht implementiert. D7 umfasst damit 15 Operationen. Der Vertrag
bindet tatsächliche Run-/Reportidentitäten sowie Modus, Snapshot, Phase und
Methodik; Befunde und Revisionsreferenzen werden begrenzt paginiert.

Strict darf nur nach vollständigem Nachweis der gebundenen Entscheidungscutoffs
STRICT_PIT_EVIDENCE_COMPLETE ausweisen. Retrospective bleibt explorativ und
behauptet keine damalige Verfügbarkeit. BLOCKED/INCOMPLETE, getrennte qualitative
Dimensionen und sämtliche Daten-/Handelsgrenzen bleiben bestehen. Vorhandene
Legacy-Schemas/Pfade und generierte Unions werden nicht erweitert. Die neue
Quant-Ergebnisressource referenziert Childruns, ohne eine unmögliche alte
Run-DTO-Projektion zu behaupten.

## Verification and synchronization

Vertragskompatibilität: evidenceStatus ist die zusätzliche PIT-/Vollständigkeits-
Achse. Bestehende eligibility-Werte werden nicht erweitert; auswertbare Forschung
bleibt wissenschaftlich EXPLORATORY_WITH_LIMITATIONS, auch bei vollständig
belegten Strict-Entscheidungscutoffs. Strict behält seinen Modus und erhält dabei
evidenceStatus=STRICT_PIT_EVIDENCE_COMPLETE/rating=PASS. Ein neuer Report zeigt
dieselben Achsen über overallStatus und evidenceStatus. INCOMPLETE/BLOCKED bleiben
unterscheidbar und insgesamt gesperrt. Kein Breakingcheck wird abgeschwächt.

Der öffentliche reportHash bindet Bericht ohne Selfhash/operative Run-/Batch-/
Report-UUIDs sowie Befunde und Revisionsreferenzen. Der physische bisherige
Storefingerprint bindet separat den gesamten Publikationsenvelope inklusive IDs.
Dadurch bleiben D4-Reproduzierbarkeit und echte FK-/Publikationsbindung erhalten.

QSL-03 umfasst die nötige additive Vertragskorrektur mit Beispielen,
Generator-/TypeScript-/Breaking-/Negativtests und echtem SQL-Reportbinding.
QSL-07 verdrahtet die 15 Routen und vorhandene Worker-/Reportlogik; QSL-08 zeigt
die neue Projektion. Requirement-/Szenarienanzahl bleibt 16/21, neue normative
Prüfdetails sind D4/D7 und vorhandenen Requirements zugeordnet. QSL-01/02 werden
nicht rückwirkend als unimplementiert ausgegeben; ihre ursprüngliche Evidenz
bleibt nachvollziehbar, die Korrektur gehört zu QSL-03.

Vor Veröffentlichung: striktes OpenSpec, unabhängiger Architektur-/Safetyreview,
Graph-/Zyklusregression, idempotenter Issue-Sync mit vollständigem Vorwärts- und
Rückwärts-Read-back. Keine automatische Archivierung oder Main-Spec-Konsolidierung.

Tatsächliche Prüfung am 05.11.: OpenSpec strict, acht Graphregressionen,
unabhängiger Plan-/Vertragsreview ohne P1/P2, 60 gültige QSL-Fixtures und
Redocly bestanden. Breakingcheck gegen main und 379ebff: null Fehler; keine
Prüfausnahme. Gesamte Vertragssuite: **2330 passed**, bestehende Starlette-Warnung.
Zwei Clientgenerierungen über 368 Dateien bytegleich; TypeScript-Fixtures und
Angular-Produktionsbuild bestanden (bestehende Budgetwarnungen). GitHub-MCP
verweigerte die Änderung mit 403; der autorisierte GH-Fallback änderte zwei
bestehende Managedblocks, legte kein Issue an und bestätigte alle 19 Kanten.
Die Wiederholung bestätigte null Änderungen und neun vollständige Read-backs.
Dieser Nachweis umfasst keine SQL-/Workerabnahme; QSL-03 bleibt offen.

## QSL-07 Runtime-Verfeinerung

Die Validierungsressource persistiert `INVALID` und `VALIDATED` mit einer
operation- und definitionsgebundenen Idempotenz. Migration 0036 speichert
den ersten Beleg je Schlüssel. Nur `VALIDATED` verweist auf die bestehende
0035-Freigabe, an die der Start gebunden ist. Migration 0035 bleibt unverändert.
Der aktuelle negative Datengate-Status ist eine separate Leseprojektion.

Ein Suchkandidat und eine Referenz können dieselbe Konfigurations-ID haben.
Die gestuften Equity- und Drawdown-Routen akzeptieren deshalb `role` und
liefern die gebundene Rolle in jeder Seite zurück. Ohne Parameter gilt die
Suchrolle, falls die Konfiguration im Grid liegt, sonst ihre eindeutige
Referenzrolle. Nichtausgewertete Suchresultate bleiben getrennt. Cursor binden
Rolle, Phase, Fold, Batch, Ergebnishash und Session.

Die gestufte Foldübersicht enthält die getrennten Aktivitätsverteilungen aus
D6. Diese Verteilungen verwenden ausschließlich vollständige, ausgewählte
OOS-Folds. Die 15 Operationen und die bisherigen Bestandsverträge bleiben
identisch. Die beiden neuen Projektionen werden mit OAS, Schema, Beispielen
und generiertem Client gemeinsam freigeschaltet.
