# Recovery und Reconciliation

Dieses Runbook gilt für lokale Fake- und spätere Paper-Läufe. PTV-04 implementiert das Ledger; Trading-, Reconciliation- und Pauseprozesse folgen später. Es folgt `REQ-RSK-002`, `REQ-RSK-005`, `REQ-RSK-007..010`, `REQ-RUN-001..005` und Design D-04 bis D-06. Grundsatz: Bei nicht beweisbarem Brokerzustand bleibt die Order `UNKNOWN`, die Reservierung bestehen und neue Risiken pausiert.

## Ausführbarer Ledgernachweis (PTV-04)

`docker compose -f infra/compose/compose.yaml --profile fake up -d postgres` startet die lokale PG-18.6-Instanz. `./.venv/Scripts/python.exe -m pytest tests/integration/test_ledger.py -q` prüft eine eingefrorene Alembicrevision und eine komplette isolierte Migration/Restore-Sequenz. Das interne Netz veröffentlicht auf dieser Docker-Desktop-Engine keinen nutzbaren Hostport; ein nichtprivilegierter, read-only Testcontainer verbindet sich deshalb intern. Nur owned Package-/Migration-/Testdateien werden read-only eingebunden, kein Docker-Socket, Rootrepository oder Credentialverzeichnis. UUID-Testdatenbanken und Login werden danach entfernt. Zufällige Testpasswörter verbleiben in Prozessspeicher/STDIN, weder in Dateien noch Argumenten oder Ausgabe.

Die Prüfung umfasst NUMERIC(38,18)/timestamptz, vollständigen TypedAnswers-Roundtrip, unveränderliche Input-/Positionsversionen, Proposal→Risk→Policy→Reservation→Intent→Order→Report, Client-/Brokerevent-Dedup, append-only Audit, tatsächliche Worker-/Execution-Rechte und konkurrierende Accountlocks. Restore vergleicht alle 18 Ledgertabellen plus Alembic-Version und alle sieben Tabellenprivilegien für vier Runtimegruppen auf 19 Tabellen (532 Rechte). Der Dump enthält nur diese Testdatenbank, keine globalen Rollen/Passwörter; ACL werden erhalten. Rollen-/Funktions-/Schemahärtung folgt der [PostgreSQL-18-Dokumentation](https://www.postgresql.org/docs/18/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).

Initialrevision `0001_ledger` ist ein statischer DDL-Snapshot. Spätere Domainänderungen benötigen neue Migrationen; historische Revisionen nicht regenerieren. Produktionsartige Migrationen erhalten ausschließlich eine getrennte Operator-Migrationsverbindung über Alembic Config.attributes.connection. Keine URL oder Credentials in alembic.ini, CLIargumenten, Logs oder Modellkontext. Ein destruktiver Downgrade ist gesperrt. Vor einer echten Wiederherstellung Runtimegruppen und minimale Rechte unter dem Migrationsadministrator vorbereiten; keine Broker-/Jev-Credentials in Backup oder Restore bereitstellen. Wiederhergestellte Handelsdaten sind keine Handelsfreigabe. Die folgenden Trading-Recoveryabläufe bleiben bis zu ihren Implementierungstasks geplant.

## Sofortige Sicherung

1. `PAUSE_NEW` über den unabhängigen lokalen Kontrollpfad setzen oder den Watchdog-Latch prüfen. **Geplante CLI-Schnittstelle, derzeit nicht vorhanden:** `tradingctl pause --reason RECOVERY`.
2. Keine erneute Submit-Anfrage senden. `clientOrderId`, interne Intent-/Proposal-/Risk-IDs, bekannte Broker-ID, letzter definitiver Fill, Ledger- und Policyversion, Trace-ID und UTC-Zeit aus redigierten internen Daten sichern.
3. DB, Execution und Broker separat auf Erreichbarkeit prüfen. Fehlt DB-Konsistenz oder Lease/Fencing, blockiert das lokale Submitgate jeden Netzwerkrequest.

## Crashmatrix

| Unterbrechung | Erwarteter Zustand | Recovery-Regel |
|---|---|---|
| Vor persistiertem `SUBMITTING` | Kein gestarteter Netzwerkrequest beweisbar | Intent/Reservation prüfen; abgelaufene Proposal-/Risk-Gates sperren Submit |
| Nach `SUBMITTING`-Commit, vor Netzwerk | Submit unbewiesen | Broker per `clientOrderId` und Fills abfragen; ohne definitiven Beweis `UNKNOWN` halten |
| Nach Brokerannahme, vor lokalem Ack | Brokerorder möglich | Niemals blind retryen; interne/brokerseitige IDs und Fills reconciliieren |
| Nach Ack, vor Outbox-Publish | Ledgeränderung dauerhaft | Outbox erneut zustellen; Consumer deduplizieren |

## Broker-Reconciliation

1. Für jede nichtterminale Order und jedes `UNKNOWN` interne und brokerseitige Order-/Fillansicht mit konsistenter Zeit-/Paginationgrenze abfragen. Die Brokerabfrage ist **künftige Execution-Funktion**, keine manuelle Order-API für Agenten oder Codex.
2. Brokerereignisse nach stabiler Ereignis-ID deduplizieren; kumulative Fillmengen nur als positive Deltas buchen. Späte definitive Fills dürfen einen terminal wirkenden lokalen Status korrigieren. Die kumulative Fillmenge bleibt monoton und höchstens Intentmenge.
3. Bei Cancel/Fill-Rennen gewinnt ein definitiver Fill für den gefüllten Anteil. Ein Cancel-Ack schließt nur den beweisbar ungefüllten Rest. Teilfüllung konvertiert proportional Reservierung in Exposure; der ungeklärte Rest bleibt reserviert.
4. Keine Order gefunden ist nur dann definitive Nichtannahme, wenn die dokumentierte Brokerregel und vollständige Order-/Fillabfrage dies belegen. Sonst `UNKNOWN`, Pause und auditierte Operatorentscheidung. Reconciliation-Abweichung sperrt neue Einstiege und Positionsvergrößerungen.
5. Erst nach konsistentem Ledger, vollständigem Health und aktueller Policyversion kann ein Operator Resume anfordern. **Geplante CLI-Schnittstelle:** `tradingctl resume --if-policy-version <VERSION>`; die spätere Implementierung muss die Gates erneut prüfen.

## DB-, Job- und Stream-Recovery

- DB-Neustart: Execution-Submit lokal sperren; Dienste dürfen keine alten Leases verwenden. Nach Wiederkehr Migrationversion, Constraints, Ledger, Outbox und Audit prüfen, dann Broker-Reconciliation durchführen. Restore startet sicher pausiert; unbekannte Orders verhindern destruktive Downgrades.
- Workerlease: Ein neuer Besitzer erhält einen neuen Fencingtoken. Der alte Besitzer darf keine Ergebnisse committen. Submitjobs werden vor Übernahme reconciliiert und nie als gewöhnlicher Retry behandelt. Nach Retrylimit landet der Job mit redigiertem Fehler im Dead Letter.
- Outbox/SSE: Nach Crash aus dauerhaftem Outboxzustand zustellen und Consumer deduplizieren. Dashboard lädt einen konsistenten Snapshot mit Cursor; bei `410` oder `reset-required` verwirft es den alten Streamzustand und lädt einen neuen Snapshot. Es behauptet keine Kontinuität bei Lücke.

## Abschluss-Evidenz

Recoveryprotokoll enthält Crashphase, Trace-ID, Intent- und brokerseitige Kennungen, letzten definitiven Fill, reserviertes Restbudget, Abfrageumfang, Audit-/Outboxversion und finale SafetyState-Entscheidung. Es enthält keine Credentials oder rohen sensitiven Brokerpayloads. Der Test `tests/recovery/test_submit_crash_matrix.py` muss alle vier Crashphasen mit höchstens einer Brokerorder je Intent und ohne Blind-Retry belegen.
