# QSL-03: Persistenz und gemeinsame Admission

`0035_quant_lab` erweitert den vorhandenen Research-Speicher. Für Strict,
Retrospective, Readiness und QSL gelten die gemeinsame Admission-Barriere und
die gemeinsame Worker-Lease. QSL reserviert anhand der aus dem kanonischen
Grid abgeleiteten Kandidatenzahl `C+2` Slots. Interne Runreferenzen zählen
innerhalb des Batches. Fences, Versuche, kumulierte Barschritte und
Publikationsbytes liegen in PostgreSQL.

Die persistente Validierung bindet eine immutable Definition an die vom
vertrauenswürdigen Control-Port erstellte Kalender- und Budgetabschätzung.
Die tatsächliche Kalenderrechnung vor dem Start wird in QSL-07 angeschlossen.
Die Datenbank kontrolliert unabhängig den 100er-Slotpool, höchstens drei
Versuche, die 295-Sekunden-Schreibgrenze, die zusammengefassten 100.000
Revisions- und Metadatenbindungen, 100.000 Barschritte und 12 MiB Publikation.
Die Rollen erhalten ausschließlich die für Research erforderlichen Rechte.

Die Altfenster `trainingStart`/`trainingEnd` dienten der Initialisierung und
belegen keine separat ausgeführte In-Sample-Analyse. Die Migration bewahrt sie
als `UNKNOWN` mit `REQUESTED`. Identifizierbare Testfenster bleiben
`WALK_FORWARD_OOS`. `RESULT_AVAILABLE` beruht auf tatsächlichen Kurvenzeilen
für den jeweiligen alten Fold. Unrekonstruierbare Intervalle bleiben
unbekannt. Wiederholte Versuche derselben Quelle erzeugen keine neue Runde.
`supersedesBatchId` ist eine immutable, instrumentgebundene Historienreferenz.
Sie lässt frühere Runden und deren Exposition bestehen.

Reliability verwendet den vorhandenen Reportstore mit expliziter QSL-Variante.
Der physische Envelope-Fingerprint umfasst die operativen IDs. Der fachliche
`reportHash` umfasst die kanonischen Reportfelder ohne Report-, Batch- und
Run-ID sowie die vollständigen, geordneten Findings und Revisionsreferenzen.
Legacy-Projektionen filtern die internen QSL-Läufe und Reports aus. Die
Research-Interpretation von `evidenceStatus` bleibt vom bisherigen
`eligibility`-Feld getrennt.

## Tatsächliche Prüfung

Die bestehende Docker-Harness migriert eine temporäre PostgreSQL-Datenbank
mit synthetischen Bestandsdaten, führt echte Rollen- und Transaktionsprüfungen
aus, exportiert per `pg_dump` und vergleicht Inhalt und ACLs nach Restore in
einer weiteren temporären Datenbank. Die synthetischen Report-Fixtures
prüfen ausschließlich die Publikationsbindung.

Der gemeinsame QSL-Harness ist ein PostgreSQL-Test. Er prüft folgende Gruppen
in derselben isolierten Migration und beim anschließenden Restore.

| Gruppe | Geprüfte Invariante |
| --- | --- |
| T-QEX-002 | Feste Source-FKs und Revisionsfingerprints. Falsche Seeds, Austausch einer echten Revision am gleichen Barstart und verspätete Strict-Cutoffs werden abgewiesen. Gebundene Revisionen lassen sich nicht löschen. |
| T-QEX-004/009 | Installierte Strategy-Pins, kanonisches Grid und unveränderliche Policy. Das atomare Selection-Set ist auch beim direkten SQL-Commit erforderlich. Gates, Validation-Inputhash, gebundene Nichtwahl und die OOS-Barriere werden vor der Veröffentlichung geprüft. |
| T-QEX-005 | Gemischte Admission aus Strict, Retrospective-Run, Retrospective-Assessment, Readiness und QSL. `C+2` für das Neunergrid, 99/100 und Ablehnung darüber. Alte Writer und veraltete Transaktionssnapshots werden abgewiesen. |
| T-QEX-006 | Tatsächliche Reclaims über drei Fences mit synthetisch vorgezogener Ablaufzeit. Keine Rücksetzung verbrauchter Barschritte oder Rückkehr zu `ACCEPTED`. Unverändertes altes Claim-SQL kann interne QSL-Kinder nicht übernehmen. UUID-Kollisionen und die versuchte Wiederbelebung einer alten Lease schaffen keine zweite aktive Lease. |
| T-QEX-007 | Aktuelle Lizenzsperre zwischen Berechnung und Veröffentlichung verhindert den terminalen Erfolg. Child, Reliability, Resultlink und bestätigter Fortschritt committen zusammen. Fremde Reliabilityreferenzen und QSL-IDs in alten Lesepfaden werden abgewiesen. |
| T-QEX-011 | Bindung des Vorgängerstands unter derselben Barriere wie die Registrierung. Altstatus und Phasenrollen bleiben konservativ erhalten. Die Vorbereitung eines Readiness-Berichts erzeugt keine Performance-Exposition. |
| Rollen und Restore | Enge ACLs einschließlich direkter Ablehnung des Readiness-INSERT durch Worker. Vergleich des neuen Provisionierungsmanifests mit den tatsächlichen Research-ACLs des isolierten Testschemas. Übereinstimmende Daten und ACLs nach Restore. |

Die PostgreSQL-Regression am Produktionscode nach der Lease-Korrektur ergab
**5 passed in 245.11s**. Enthalten sind der kombinierte QSL-Harness und die
vier separaten Legacy-Storage- und Recovery-Targets.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_quant_lab_storage.py tests/integration/test_backtest_jobs.py tests/integration/test_retrospective_storage.py tests/recovery/test_readiness_jobs.py tests/recovery/test_retrospective_jobs.py --tb=short
```

Die bestehende RRB-Upgradeassertion vergleicht die Altwerte nach Ausblendung
der additiven Spalten und prüft `quant_batch_id IS NULL` separat. Am alten
Strict-Pfad bleibt der bestehende Fehler `UNAVAILABLE` bei vollem Queuepool.

Die gezielten lokalen Domänen-, Projektions- und Vertragstests ergaben
**533 passed in 54.38s**.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_backtest_storage.py tests/unit/test_quant_lab_models.py tests/unit/test_quant_lab_grid.py tests/contracts/test_quant_lab_examples.py --tb=short
```

Die beiden Legacy-API-Harnesses bestanden auch gegen den finalen
Produktionsstand mit der Lease-Sperre. Ergebnis **2 passed in 93.75s**.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_backtest_api.py tests/integration/test_retrospective_api.py
```

Der abschließende QSL-Harness enthält zusätzlich eine unter 0034 persistierte
Retrospective-Assessment samt Dataset und Preview-Report sowie einen
Retrospective-Run mit Status `FAILED`. Der Vergleich nach 0035 bestätigt
die unveränderte Runidentität und die registrierten Rollen `UNKNOWN` und
`WALK_FORWARD_OOS` mit `REQUESTED`. Ergebnis
`PASS quant PG upgrade/legacy-unknown/policy/candidate/atomic-admission/queue100/fence/recovery/roles/restore`.

```powershell
python -c "from tests.integration.test_quant_lab_storage import host; print(host())"
```

Ruff und `git diff --check` waren sauber. Der Git-Vergleich für Migrationen
0001–0034 zeigte keine Änderungen. Das unabhängige Review von Migration
`0035_quant_lab.py` mit SHA-256
`4f57822ac13ff4660d5e9599bc2a31ec8c13d52223605809b8dd3ba965b06f75`
bestätigte die Behebung der P1- und P2-Befunde.
