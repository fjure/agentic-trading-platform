# RDR-03: Persistenz und grundlegende Nachweisverwaltung

Migration `0030_readiness_storage` folgt unverändert auf `0029_backtest_jobs`.
Sie ergänzt Evidenzrevisionen, versionierte Vertrauensentscheidungen, Importreceipts,
separate Commitbestätigungen, redigierte Ablehnungsaudits sowie die Basistabellen
für Diagnose und Vorwärtsbeobachtung. Alte Migrationen und Finanzprivilegien
werden nicht geändert. Das Produktionsregister wird leer angelegt.

`EvidenceStore.append_bundle` nimmt ausschließlich typisierte normalisierte
Einträge an. Ein SQL-Aufruf sperrt den gemeinsamen Truststand, prüft die erwartete
Trustversion und schreibt alle Einträge und den Receipt mit derselben reservierten
Researchversion und derselben tatsächlichen Datenbankaufnahmezeit. Fremde
`ingestedAt`-/`commitVersion`-Felder existieren im Eingabetyp nicht. Ein Konflikt,
eine ungültige Revision oder eine Quellenbeobachtung nach der Aufnahme rollt das
gesamte Bündel zurück. Identischer Inhalt erhält auch nach einer Truständerung
denselben ursprünglichen Receipt; ein bereits gebundener Idempotenzschlüssel
kann nicht auf einen anderen bestehenden Receipt zeigen.

`confirm_commit` benötigt eine neue Transaktion, die den zuvor geschriebenen
Receipt bereits sieht. Die Datenbank vergleicht die tatsächlichen Transaktions-IDs
und erzeugt eine neue unveränderliche Bestätigung mit ihrer eigenen Uhr. Das ist
eine konservative Bestätigungszeit nach Commit, kein behaupteter exakter
PostgreSQL-Commitzeitpunkt. Ein unklarer oder fehlender Bestätigungsaufruf bleibt
unbekannt. Aufnahmereceipts dürfen widersprüchliche lokale Uhrpaare erhalten,
damit die Diagnose sie als ungültige Messfälle ausweisen kann; das lockert weder
die Importgrenze noch die Sichtbarkeit gespeicherter Barrevisionen.

`read_trust_snapshot` bindet eine monotone Trustversion und liefert einerseits
sichtbare Datenrevisionen und andererseits den aktuellen Vertrauensstand. Aktuelle
Quellen- und Zeugenentscheidungen berücksichtigen Widerruf, Kompromittierung,
Ablauf und eingeschränkte Geltungsbereiche auch bei einem alten Datensnapshot.
Die gebundene Signierzeit muss weiterhin im aktuellen Gültigkeitsintervall liegen;
der gespeicherte KEY-Fingerprint muss zum weiterhin gepinnten öffentlichen
Schlüssel passen. Ein `keyId` darf seinen Schlüssel, Algorithmus oder Autoritätstyp
nicht ändern; Schlüsselrotation erfordert eine neue Identität. Fehlt die
Registrierung für eine als verifiziert gespeicherte externe Quelle, bleibt ihre
Verwendung gesperrt.
Revisionen, Entscheidungen, Receipts, Commitbestätigungen und veröffentlichte
Berichte sind append-only; eingefrorene Berichte werden nicht umgeschrieben.
Kalenderadapter geben importierte Sitzungen mit ihrer echten Aufnahmeversion und
Evidenz-UUID zurück. Fehlende oder unbekannte externe Nachweise bleiben sperrend.

Das Bündel auf der äußeren Importgrenze bleibt auf 1 MiB und 1000 Einträge
begrenzt. Der interne Persistenzport hat eine separate Grenze von 12 MiB, da
normalisierte Provenienz und fünf Vertrauensresultate die Eingabe vergrößern.
Die äußere Bytegrenze und die kryptografische Prüfung gehören zu RDR-09.
RDR-03 importiert keine Signaturbibliothek, erzeugt keine Schlüssel, ruft keine
Quelle auf und attestiert keine realen Nutzungsrechte.

| Rolle | Neue Rechte | Ausdrücklich nicht erteilt |
| --- | --- | --- |
| `atl_evidence_importer` (NOLOGIN) | Nur Evidenz-/Trust-/Receiptlesezugriff und vier festgelegte append-only SQLports einschließlich redigiertem Ablehnungsaudit | Finanzdaten, Finanzfunktionen, Jobs, Pläne, Quellendatenwrites, direkte Belegtabellenwrites |
| `atl_backtest_worker` | Quellen-/Evidenzlesen; Diagnosejobs und unveränderliche Berichte/Befunde schreiben | Belegimport, Trustentscheidungen, Plan-/Sammlungswrites, neue Finanzrechte |
| `atl_control_api` | Diagnose-/Plan-/Evidenzprojektionen lesen | Direkte Evidenz-, Trust-, Report- oder Planwrites; separate kontrollierte Mutationsports folgen in RDR-05/06 |
| `atl_execution` | Plan-/Fälligkeitszustand, unveränderliche Beobachtungsreceipts und deren Commitbestätigung | Zertifizierte Evidenz-/Trustwrites oder Bestätigung von Importreceipts |
| `atl_worker`, `atl_watchdog` | Keine neuen Schreibrechte | Sämtliche Import-/Trustports |

Die bestehende Paperrollenaufteilung bleibt erhalten. Es gibt keinen neuen
Runtimeprozess, keinen zusätzlichen Credentialmount und keine neue Verbindung
zum Provider oder Broker; Compose benötigt für diese Basismigration keine Änderung.

Die PostgreSQL-Tests verwenden ausschließlich UUID-benannte temporäre Datenbanken,
generierte Testzugänge über private Standard-Eingabe, das vorhandene lokale
PostgreSQL18-Testnetz und lokale synthetische Metadaten. Der Restore wird in einer
zweiten temporären Datenbank ausgeführt; Inhaltsfingerprints der neuen Tabellen
und bestehende Finanzprivilegien werden verglichen. Keine Volumes werden gelöscht.

Verifizierter lokaler Prüfstand: beide PostgreSQL-Einstiegspunkte bestanden.
Nach den letzten Reviewkorrekturen bestand der vollständige Storageguest über
`test_readiness_storage.py` in 63,20 Sekunden und anschließend über
`test_readiness_roles.py` einschließlich gegenseitig gesperrter Receiptbestätigung
in 63,44 Sekunden. Die zwölf bestehenden Kalenderstoragetests bestanden ebenfalls;
Ruff meldete für alle geänderten Pythondateien keine Befunde. Der vollständige
Storageguest enthält dabei auch Upgrade, ACLvergleich und Backup/Restore.

Prüfbefehle:

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_readiness_storage.py tests/security/test_readiness_roles.py -q
.venv/Scripts/python.exe -m ruff check packages/research/evidence_storage.py packages/research/calendar_storage.py services/control_api/migrations/versions/0030_readiness_storage.py tests/integration/test_readiness_storage.py tests/security/test_readiness_roles.py
```

Der Integrationstest umfasst Upgrade ab dem bisherigen Head, unveränderte
Finanz-ACLs, gemeinsame DBzeiten/-version, atomaren Rollback, Wiederholung,
konfliktbehaftete Identitäten, append-only Revisionen, verspäteten Commit,
konkurrierenden Import, getrennte Commitbestätigung, tatsächliche SQLverweigerung,
aktuellen Widerruf bei altem Snapshot und Backup/Restore. Dies bestätigt lokale
Softwaregrenzen; eine reale Quellenzulassung oder historische Datenverfügbarkeit
wird dadurch nicht bestätigt.

Dokumentationsgrundlage bei ausgeschöpftem Context7-Kontingent:
[SQLAlchemy Transaktionen](https://docs.sqlalchemy.org/en/20/core/connections.html#using-transactions)
und [PostgreSQL18 Transaktionssperren](https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).
