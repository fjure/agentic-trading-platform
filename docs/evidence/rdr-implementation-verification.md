# Research Data Readiness: integrierte Offlineabnahme

Stand: 27.10.2026. Dieser Nachweis beschreibt ausgeführte lokale Tests mit
synthetischen Quellen, Testautoritäten und isolierten PostgreSQL-Datenbanken.
Reale Providerrechte, echte Providerlatenzen und die reale historische Eignung
von SPY-/QQQ-Daten bleiben unverifiziert. Standarddispatch und Produktionstrust-
Register werden durch diese Tests nicht aktiviert oder befüllt. Es wurden keine
echten Provideraufrufe, Brokerorders oder Deployments ausgeführt.

## Gemeinsamer tatsächlicher Pfad

`tests/integration/test_readiness_acceptance.py` verwendet Migrationen bis Head,
echte Control-/Execution-/Diagnoseworker-/Importer-SQLrollen und die öffentliche
authentifizierte FastAPI-Anwendung. Die beiden Pläne starten deaktiviert;
Aktivierung ohne Globalgate bleibt BLOCKED, eine versionierte Pause wird
persistiert. Fehlende Sitzung und fremder Origin erzeugen keinen Diagnoseauftrag.
Wiederholte Diagnose-POSTs liefern dieselbe Annahme, geänderte Nutzlast mit
demselben Schlüssel 409.

Das ausdrücklich synthetische historische Bestandsfixture ergibt ELIGIBLE und
vollständige Fenster. Ein Bericht erzeugt keinen Backtest. Erst ein gesonderter
Backtest-POST wird vom selben tatsächlichen Worker erfolgreich verarbeitet.
Ein heutiger QQQ-Backfill durch den echten Alpaca-Adapter mit ausschließlich
lokalem HTTPdouble behält heutige DB-Ingestion. Vergangene Signalzeitpunkte und
unbekannte Kapitalmaßnahmen werden dadurch nicht rehabilitiert; der explizite
Backtest bleibt BLOCKED. Befunde werden durch tatsächliche HTTPpagination gelesen;
ein manipulierter Cursor wird abgewiesen.

Ein echter Ed25519-Import ohne unabhängigen Zeitnachweis speichert getrennt
VERIFIED-Quellauthentizität und UNKNOWN-Zeitvertrauen. Er macht den Backfill nicht
zulässig. Manipulierte signierte Inhalte und neue Inhalte einer widerrufenen
Testautorität werden abgewiesen.

Für die Widerrufsprojektion importiert der Test zusätzlich einen echten,
unabhängig bezeugten Kalender. Zuerst wird die heutige DB-Ingestion nachgewiesen.
Danach setzt ausschließlich der Migrationseigentümer der wegwerfbaren Test-DB
die lokale Sichtbarkeit dieses einen synthetischen Fixtures in die Vergangenheit;
der Immutable-Trigger wird in derselben Transaktion wieder aktiviert. Signierte
Inhalte und Vertrauensdimensionen bleiben unverändert. Dies ist ein kontrolliertes
historisches Testfixture, keine Fähigkeit des Importers und kein historischer
Produktionsnachweis. Kalender-Vollständigkeit bleibt UNKNOWN; der Bericht wird
dadurch nicht ELIGIBLE.

Der echte Assessor verwendet die importierte Kalenderreferenz und persistiert
ihre Bindung. Ein erst künftig wirksamer Witnesswiderruf und eine andere Quelle
mit gleichem Schlüsselbezeichner erzeugen keine falsche Warnung. Aktuelle Witness-
und Quellwiderrufe erscheinen mit den jeweiligen exakten Decision- und
Evidenzreferenzen sowie aktueller Trustversion. Der ursprüngliche Bericht bleibt
bytegleich; sein ursprünglicher Findingscursor bleibt an ursprünglichen Report,
Snapshot und Truststand gebunden. Eine feste Whitelist aus 29 öffentlichen
Finanztabellen wird vor und nach der Kette anhand SHA256-Fingerprints sämtlicher
deterministisch sortierter JSON-Zeilen verglichen. Sie umfasst Konten,
Portfolio-Cash, Bestände/Versionen, Vorschläge, Risikoentscheidungen/-reservierungen,
Orders/Executionreports, Aktivitätsbuchungen, Initial-/Kostenbasen, Reconciliation,
Workflow und Audit. Eine ausdrücklich synthetische deaktivierte Finanzbasis
enthält dabei bereits Konten, Cash, Positionen und Auditzeilen; die Prüfung
vergleicht nicht nur leere Tabellen oder Zeilenzähler.

## Tatsächliche Last- und Rollenbegrenzung

Der zweite Integrationstest hält den echten Assessor in einem CPUcheckpoint aktiv,
während zwei weitere Diagnosen warten. Währenddessen ist kein Worker-SQLpoolslot
ausgecheckt. Paper-PAUSE, KILL, Reconciliation und Workerheartbeat funktionieren;
die Kontrollen schließen innerhalb von fünf Sekunden ab, während die Diagnose
weiterarbeitet. Nach beiden Kontrollantworten müssen der öffentliche Zustand
HALTED, das Entrygate geschlossen und der getrennt mit Execution-SQL gelesene
HALTED-Kill-Latch wahr sein. Elf stabile Finanz-/Order-/Fill-/Bestands-/Kostenbasis-
Tabellen bleiben anhand vollständiger Zeilenfingerprints unverändert. Erlaubte
Status-, Audit-, Outbox- und Reconciliation-Snapshotänderungen der tatsächlich
ausgeführten Sicherheitsaktionen sind von dieser zweiten Whitelist getrennt.
Anschließend publiziert genau ein tatsächlicher Diagnoseauftrag. Das Paperfixture
verwendet ausschließlich synthetische GET-Provider; null Orders.

`tests/security/test_readiness_isolation.py` startet den gemeinsamen Backtest- und
Diagnoseworker tatsächlich als UID10001 mit höchstens 0,5 CPU und 256MiB RAM, nur
internen Docker-Netzen und Read-only-Quellmounts. Der bestehende HBA-Test bindet
die Datenbankidentität an dieses Netz. Provider-/Secret-/Docker-Socketmounts fehlen.
Beide tatsächlichen Jobarten werden erfolgreich verarbeitet. HTTP-Sends sind
verboten, Finanzzugriff und Schreiben in den Import-/Ablehnungsbestand schlagen
mit der wirklichen SQLrolle fehl. Die bestehende Backtestisolationshilfe erhielt
nur optionale Modul-/Markerparameter; ihre bisherigen Defaults bleiben erhalten.

## Ausgeführte Prüfungen und Artefakte

- `pytest tests/integration/test_readiness_acceptance.py
  tests/security/test_readiness_isolation.py -q`: **3 bestanden, 63,71s**.
  Dieser Durchlauf enthält die tatsächliche Importreferenz-/Witness-/Quellen-
  Widerrufsprojektion und die beiden Last-/Isolationsprüfungen.
- Anschließend wurde die gemeinsame Kette mit explizitem BLOCKED-Ergebnis und
  getrennten OBSERVED_TOO_LATE-/CORPORATE_ACTIONS_UNKNOWN-Befunden verschärft:
  **1 bestanden, 32,24s**. Keine Runtimeänderung für diese Verschärfung.
- Nach unabhängigem Review wurden vollständige Finanzzeilenfingerprints sowie
  zwingend HALTED und wahrer Kill-Latch ergänzt. Beide betroffenen tatsächlichen
  Integrationsketten erneut: **2 bestanden, 43,13s**.
- Ruff für beide neuen Testdateien und die parametrisierte bestehende
  Isolationshilfe: bestanden.
- Frischer lokaler Normalimagebuild über
  `infra/containers/Dockerfile` mit `uv sync --frozen --no-dev`: bestanden.
  Anschließend frischer Testimagebuild über
  `infra/containers/research-test.Dockerfile`: bestanden.
  Die Builds installierten unter anderem `certifi==2026.10.19`,
  `cryptography==50.0.2` und `rfc8785==0.1.4` aus dem aktualisierten Lock.
  Testläufe mounten aktuellen Repositorycode read-only; kein bestehender
  Anwendungscontainer wurde neu gestartet. Die Imagebuilds bescheinigen daher
  keine Aktualisierung bereits laufender Dienste.

Ergänzende ausgeführte Paketprüfungen und ihre Grenzen:
[Verträge](rdr-01.md), [Assessor/Parität](rdr-02-domain.md),
[Persistenz, Rollen und Restore](rdr-03-storage.md),
[Scheduler, Pausenbarriere und Recovery](rdr-04-collection.md),
[Diagnosequeue und HTTP](rdr-05-api.md), [Plansteuerung](rdr-06-api.md),
[Kryptografie und atomarer Import](rdr-09-import.md).
Diese Paketnachweise ersetzen die gemeinsame Kette nicht; die gemeinsame Kette
ersetzt ihrerseits keine unbekannten realen Quellenrechte oder damaligen
Verfügbarkeitsbeweise.

## Weitere finale Prüfungen des Hauptagenten

Der Hauptagent führte am 27.10.2026 zusätzlich aus:

- Vollständige Contractsuite: **1531 bestanden, 45,43s**, einschließlich exaktem
  Runtime-Routenabgleich aller fünf neuen Operationen.
- Validator: **36 HTTP- und 7 Bündelbeispiele**, OAS **63 Operationen**.
- Zwei vollständige Clientgenerierungen sowie bestehender Appclient:
  **251 Dateien SHA-bytegleich**, TypeScriptkompilierung bestanden.
- `oasdiff` gegen Vertragsbasis `892f`: **0 Fehler**; eine Warnung zur
  allOf-Zweigzuordnung bei bestehenden unveränderten Semantiken, kein neuer
  Contractsource-Drift.
- Unitregression: **1073 bestanden, 1 plattformbezogener Skip, 12,70s**;
  die Linux-tmpfs-Prüfung wurde separat im tatsächlichen Dockercontainer geprüft.
- Sourcescan: **1119 Dateien, 0 Credentialmusterbefunde**.
- Strikte OpenSpec-Validierung: bestanden.

Die echte neue Browserabnahme mit PostgreSQL/API/HTTPdouble bestand mit einem
Test in 33,9s und anschließendem SQLnachweis; acht synthetische Fälle bestanden
in 10,7s. Die bestehenden Research-/Backtest-/Paperbrowser prüfte der Hauptagent
zusätzlich erfolgreich (2 / 4 / 1 Tests). Ein paralleler Paperlauf war zuvor am
sicheren Reconciliationgate stehengeblieben; der isolierte Wiederholungslauf
bestand ohne Änderung an Produktionsgates. Die genaue Ursache des zeitabhängigen
Testlaufs blieb unbestimmt; siehe RDR-07-Nachweis.

Finaler Vertragswiederholungslauf: **1531 bestanden, 35,07s**; abschließender
Sourcescan **1123 Dateien, 0 Credentialmusterbefunde**. Normal- und Testimage
wurden nach der letzten Runtimekorrektur nochmals mit Frozen-Lock gebaut, ohne
Deployment oder Neustart bestehender Dienste. Issue-Read-back und geprüfte
Gitintegration werden separat dokumentiert. Main-Spec-Synchronisierung und
Archivierung benötigen weiterhin ihren eigenen Auftrag.

## OpenSpec-Verify: Vollständigkeit, Korrektheit und Kohärenz

Geprüfter Change: `research-data-readiness`, Schema `spec-driven`. Proposal,
beide Deltaspezifikationen, Design D1–D8, Taskdatei und tatsächliche Code-/Teststände
wurden abgeglichen. Die folgende Zuordnung umfasst alle zwölf hinzugefügten
Requirements und alle 29 benannten Szenarien. Die Paketnachweise enthalten die
konkreten Befehle und Ergebnisse; synthetische Testautoritäten gelten ausschließlich
in wegwerfbaren Testdatenbanken.

| Requirements / Szenarien | Implementierung und ausgeführte Prüfevidenz |
| --- | --- |
| REQ-RDR-001; T-RDR-001/002 | Gemeinsamer Assessor und Materialisierer in `packages/backtesting/readiness.py` und `dataset.py`; Paritätsmatrix `tests/unit/test_readiness_assessment.py`; tatsächliche Diagnose-/Backtestkette `test_readiness_acceptance.py`. Heutiger Backfill bleibt BLOCKED. |
| REQ-RDR-002; T-RDR-003/004 | Unabhängige Befundaggregation, explizite Folgeprüfungen und Publikationsbudgets; Unitfälle zu unbekanntem Kalender und Mehrfachblockern, tatsächliche atomare Publikation und Cursorpagination in `test_readiness_api.py` / `test_readiness_jobs.py`. |
| REQ-RDR-003; T-RDR-005/006 | Geschlossenes Bündelschema, Ed25519/RFC8785 und leeres Produktionsregister; `test_research_evidence_trust.py`, tatsächliche Rollback-/Parallelimportprüfung `test_research_evidence_import.py`. |
| REQ-RDR-004; T-RDR-007/008 | Append-only Revisionen und gemeinsame Coverage-/Qualitätsgates; Unitfälle zu negativen Korrekturen, Inhaltsrückkehr und unzureichender Teilauflösung, tatsächlicher PG-Revisionsimport. |
| REQ-RDR-005; T-RDR-009/010 | Auth/Origin vor Diensten, dauerhafte Idempotenz, snapshotgebundene Seiten und getrennte aktuelle Widerrufswarnungen; HTTP-/Transporttests und echte Browserprüfung. |
| REQ-RDR-006; T-RDR-011/012 | Tatsächliche SQLrollen, frische begrenzte Workercontainer, reale Assessorlast neben Paper-Safety, vollständige Ledgerfingerprints und dieser begrenzte Offlineabschluss. |
| REQ-RDR-007; T-RDR-013/014/015 | Fünf unabhängige Vertrauensdimensionen, zeitgebundene unabhängige Witnessprüfung, tatsächlicher DB-Stempel; Kryptografie-/Import-/Assessor-Suiten und konservative integrierte importierte Kalenderdiagnose. |
| REQ-RDR-008; T-RDR-016/017/018 | Aktueller versionierter Trust und unveränderliche ursprüngliche Berichte; vollständige Statusmatrix, PG-Persistenz-/Restoreprüfung, tatsächliche Source-/Witnesswiderrufe einschließlich Scopekollision und zukünftiger Wirksamkeit. |
| REQ-HMD-007; T-HMD-015/016 | Execution-Scheduler und bestehender UPDATEpfad; echte lokale Kalender-/Bardoubles, deaktivierter Standard und sichtbare Rechte-/Credentialblocker. |
| REQ-HMD-008; T-HMD-017/018 | Eindeutige Fälligkeiten, zwei Schedulerprozesse, gültiger Besitzer/Fence/Lease, Wiederaufnahme und gemeinsame Planlock-Pausenbarriere; tatsächliche PG-Sammlung, Recovery und versionierte Plan-API. |
| REQ-HMD-009; T-HMD-019/020/021/022 | Empfang vor Normalisierung, echte DB-Ingestion und separate Commitbestätigung; Unitfälle zu Fristgrenzen, zehn Barnennern, Nullmessung, ungültigen Uhren und A→B→A; PG-Receiptadapter und Sammlung ohne Importinstallation. |
| REQ-HMD-010; T-HMD-023/024/025 | Maximale getrennte Signal-/Outcomeläufe, sichtbare Kalenderrevisionen, DST/Earlyclose und rollendes Warm-up; entsprechende Assessor-Unitfälle und Materialisiererparität. |

Designkohärenz: D1 nutzt gemeinsame Primitiven; D2 denselben isolierten Worker;
D3/D7 den getrennten Offlineport; D4 ausschließlich Execution für Provider;
D5 den kanonischen Vertrag und generierten Materialclient; D6 begrenzte
Dateizuständigkeiten und tatsächliche Tests; D8 keine Fristquote als Ersatz für
individuelle Strict-PIT-Prüfung. Keine zweite Zulässigkeitslogik oder automatische
Handelsaktivierung wurde eingeführt.

## Abschließender Safetyreview

| Invariante | Befund und konkrete Evidenz |
| --- | --- |
| Agenten-/Brokerisolation und LIVE | Keine neue Orderfunktion; Diagnoseworker ohne Provider-/Credential-/Finanzrechte. Tatsächlicher Container-/SQLtest und unveränderte Risk-/Executiongates; LIVE weiterhin ausgeschlossen. |
| Fail-closed und Wiederaufnahme | Globale Sammlung aus, Produktionstrust leer, alte Fences und ausgeschiedene Generationen gesperrt; Recovery-/Rollen-/HTTPtests. |
| Decimal und UTC | Gemeinsame Domainmodelle und geschlossene UTC-/Decimalverträge; vollständige Unit-/Vertragssuiten. Keine Brokerfloatkonvertierung in neuen Pfaden. |
| Unabhängiges Pause/Kill/Reconciliation | Reale Assessor-CPUlast mit zwei Jobs; Paper muss HALTED, Entrygate geschlossen und Execution-Killlatch gesetzt melden. Verschärfter PG-Test bestanden. |
| Credentials und Redaktion | Execution bleibt alleiniger Alpaca-Konsument; Offlineimport verwendet keine Runtimecredentials. Auth/Origin, feste Relays und Sourcescan ohne Befund; ausschließlich synthetische Testwerte. |
| Finanzbudget, unbekannte Orders, Cancel/Fill | Keine Änderung der Risikoreservierungs-/Submit-/Reconciliationlogik. Bestehende Unitfälle für Paper-Submit, Risk, Reconciliation und Cancellation bestanden; Offlinepfad lässt alle 29 erfassten Ledgertabellen bytegleich. |
| Unveränderlichkeit und Audit | Reports, Befunde, Trust-/Planrevisionen und Importreceipts append-only; exakte ACLmanifeste, atomare Publikations-/Rollback-/Restoretests und aktuelle getrennte Warnungen. |

Unabhängige Reviews: Sammlungs- und Importreview durch jeweils andere Bearbeiter;
Diagnose-/Planreview durch den Hauptagenten; UI- und Gesamt-Safetyreview durch einen
weiteren Agenten. Zwei P2-Prüflücken der Gesamtprüfung wurden korrigiert und mit
zwei tatsächlichen PG-Tests in 43,13s erneut geprüft. Abschließender unabhängiger
Re-Review: keine verbleibenden P1/P2. Keine offene fachliche Abweichung im
offline prüfbaren Umfang. Reale Providerrechte, Uhrsynchronisierung und historische
Publikationsnachweise bleiben ausdrücklich unverifiziert.

## Finaler Stand des Hauptagenten

Nach dem letzten Frozen-Lock-Neubau von Normal- und Testimage bestand die
gesamte tatsächliche PG-/HTTP-/Import-/Paper-Safety-/Workerisolationssuite erneut:
**3 Tests bestanden in 47,13s**. Finale Vertragssuite: **1531 bestanden in 35,07s**.
Alle 79 geänderten Quell-/Artefaktdateien bestanden eine strikte UTF-8-Prüfung.
Die zwölf Requirements und 29 Szenarien sind oben zugeordnet; alle neun
OpenSpec-Tasks sind implementiert und offline abgenommen. Die UI- und
Gesamtreviews haben keine verbleibenden P1/P2-Befunde. Bekannte Grenzen bleiben
reale Providerrechte, damalige Veröffentlichung und der dokumentierte
zeitabhängige parallele Paper-Testlauf. Die Issue- und Gitnachweise liegen in
`docs/development/github-readiness-issue-sync.json` und
`docs/development/github-readiness-git-integrations.json`.
