# RDR-06: versionierte Sammlungssteuerung

Stand: 27.10.2026. Die beiden authentifizierten HTTPoperationen lesen höchstens die
registrierten SPY-/QQQ-Pläne und persistieren ausschließlich den versionierten
Sammlungswunsch. Der Wunsch verändert weder Globalenable noch Nutzungsrechte,
Credentials, Handel oder Backtestaufträge. Ohne Globalenable bleibt Aktivierung
`BLOCKED`; Providerverifikation bleibt unabhängig `UNVERIFIED`.

Migration `0032_collection_control` ergänzt einen unveränderlichen Commandbestand
und einen eng begrenzten SECURITY-DEFINER-Port mit festem Searchpath. Nur Control
darf diesen Port ausführen; Control erhält keine direkten Planwrites. Planversion,
Generation, Revisionszeile und vollständige ursprüngliche Replayantwort werden
atomar gespeichert. Der identische Idempotency-Key liefert diese Antwort auch nach
weiteren Planänderungen und abgeschlossenen Abrufen; anderer Inhalt und veraltete
Version ergeben getrennte 409-Codes. Pause nimmt denselben Planlock wie
Dispatchzulassung, retiert unbegonnene ältere Fälligkeiten und bestätigt erst nach
Commit. Bereits begonnene STARTED-Abrufe bleiben zählbar und dürfen ihre echte
Antwort später speichern.

GET berechnet Beobachtungsmetriken über höchstens die letzten 24 Stunden mit
gemeinsamen Assessorprimitiven und unveränderten realen sechs Zeitdimensionen.
Unbekannte Kalendertage werden nicht als erwartete Bars erfunden. Barfenster
verwenden die gemeinsamen Fristen; ohne Nutzerprotokoll bleiben Strict-PIT- und
Experimentfenster leer, `complete=false`, mit
`EXPERIMENT_PROTOCOL_NOT_BOUND` in den Metrikgrenzen. Das ist keine Prognose und
keine Bescheinigung zukünftiger Dateneignung. CPUbudget 1s und SQLtimeout 3s
begrenzen die Projektion. Eine fehlgeschlagene Statistiktransaktion blockiert
Pause nicht: sie wird zurückgerollt; die Pause speichert konservativ leere
Metriken/Fenster und `complete=false`. Auch GET hält die Plansteuerung bei
Metrikproblemen lesbar: ein Savepoint rollt die fehlgeschlagene Statistik innerhalb
des gemeinsamen Repeatable-Read-Snapshots zurück, die Projektion zeigt
`METRICS_UNAVAILABLE` und leere unvollständige Messungen. So bleibt Pause nach
einem frischen Laden bedienbar. Aktivierung schlägt bei Statistikfehlern mit 503
fehl; Fehler der grundlegenden Planlesung bleiben ebenfalls 503.

Prüfevidenz:

- `tests/integration/test_research_collection_api.py`: echte isolierte PostgreSQL-
  Migration bis Head, reale FastAPI-Session-/Origin-/UUIDgrenzen, Defaultlesung ohne
  Plananlage, aktuelle Metriken ohne vorherigen Diagnoseauftrag, BLOCKED/Pause,
  konkurrierendes CAS, Replay nach Fortschritt, Budget- und SQLrollback bei GET/Pause,
  gemeinsame Planbarriere, STARTED-Zähler, Ausschluss späterer alter Zulassungen,
  ACLs und keine Broker-/Backtestwrites. 1 Test bestanden in 74,58s; anschließend
  vollständiger Dump/Restore mit dem bestehenden isolierten Storageharness.
- `tests/contracts/test_collection_transport.py`: geschlossene rekursive DTOs,
  keine bool/int/Versionskoerzierung oder Gateoverride, Duplicatekeys/NaN und
  16MiB-Grenze, keine unvereinbarte If-Match-Steuerung, Auth vor Servicezugriff,
  sichere Fehler, strikte Ausgabevalidierung und fester privater Relay mit Trace
  und Idempotency-Key ohne Ambientauth. 26 Tests bestanden in 2,04s. HTTPdoubles sind ausschließlich
  synthetische Transporttests; der PostgreSQLtest oben ersetzt sie nicht.
- Ruff prüft Service, DTOs, Routen, Migration und beide Testdateien.

Keine echten Provider, Schlüssel oder Zertifizierer wurden benötigt. Die
Oberflächenabnahme und die übergreifende unabhängige Abnahme werden separat in
RDR-07/RDR-08 dokumentiert.

## Unabhängige Integrationsprüfung

Der Hauptagent prüfte den SQLport, aktuelle Rollen, gesperrten Suchpfad,
Compare-and-swap, unveränderliche ursprüngliche Replayantwort und den gemeinsamen
Planlock. Eine gefundene Abhängigkeit der Pause von der Metrikauswertung wurde
beseitigt: eigener zurückrollbarer Auswertungsschritt, konservativ leere und
unvollständige Metriken bei Budget-/SQLfehlern; die Pausenmutation bleibt möglich.
Beide Fehlerfälle wurden in der tatsächlichen PostgreSQLsuite geprüft. Keine
offenen P1/P2 im geprüften Planumfang. Der Hauptagent bestätigte die 26
Transportprüfungen separat in 2,04s. Das strikte Berechtigungsmanifest ergänzt
ausschließlich die drei 0032-Lese-/Funktionsrechte für Paper-Control.

Vollständige Contractsuite einschließlich aller fünf neuen Runtimeoperationen:
**1531 bestanden, 45,43s**. Der zusätzliche GET-Fehlerpfad wurde anschließend
mit tatsächlichem PostgreSQL/HTTP/Restore erneut geprüft: **1 bestanden, 74,58s**.
Auch ein frischer Browserclient erhält bei erschöpfter Metrikprüfung die
Planmetadaten mit METRICS_UNAVAILABLE und unvollständigen leeren Metriken;
SQLfehler werden im Savepoint zurückgerollt. Pause bleibt erreichbar.
