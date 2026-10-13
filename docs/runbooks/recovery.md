# Recovery und Reconciliation

Dieses Runbook gilt für lokale Fake- und spätere Paper-Läufe. PTV-10 implementiert den einmaligen Fake-Submit mit dauerhaftem `SUBMITTING`; PTV-11 implementiert die lesende Fake-Reconciliation über `services.execution.reconciliation.reconcile_order`. Der unabhängige Betriebs-/Resume-Pfad folgt in PTV-12. Es folgt `REQ-RSK-002`, `REQ-RSK-005`, `REQ-RSK-007..010`, `REQ-RUN-001..005` und Design D-04 bis D-06. Grundsatz: Bei nicht beweisbarem Brokerzustand bleibt die Order `UNKNOWN`, die Reservierung bestehen und neue Risiken pausiert.

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

1. Execution hält den Accountlock und prüft die lokale Order, das unveränderliche Intent-/Risk-/Reservation-Geflecht, den einzigen Paper-Account und beide Brokerkennungen. Für den exakten lokalen `FakeBrokerAdapter` liest `reconcile_order(ledger=..., account_id=..., order_id=..., broker=...)` ausschließlich Orderstatus und Reports; die Fixture-Methode `reconcile` löst eine bloß transiente Ungewissheit anhand ihres gespeicherten Zustands. Sie sendet, storniert und wiederholt keine Order. Ein neuer Fake-Prozess ist kein Beweis für die Nichtannahme einer Order des alten Prozesses.
2. Brokerberichte werden per `(adapter, broker_event_id)` dedupliziert. Die wirtschaftliche Signatur umfasst Brokerereignis, Status, kumulative Menge, Durchschnittspreis, Gebühr, Zeitpunkt und lokale Order-/Trace-ID; reine Empfangszeit und Quellbericht-ID beeinflussen sie nicht. Gleiche Ereignis-ID mit verändertem Inhalt oder gleiche kumulative Menge mit widersprüchlichem Preis führt zu `UNKNOWN` und `PAUSE_NEW`. Fehlender wirtschaftlicher Durchschnittspreis bei einem neuen Fill bleibt ungeklärt. Der Fake hat keinen Gebührenmechanismus; `fee=null` bedeutet ausschließlich für diese Fixture null Gebühren, eine von null verschiedene Gebühr wird abgelehnt.
3. Nur positive, belegte kumulative Fill-Deltas ändern die Position. Kaufkosten, durchschnittlicher Einstand, Verkaufserlös und realisierter Gewinn/Verlust müssen ohne Rundung als `NUMERIC(38,18)` darstellbar sein. Jeder Fill erzeugt eine neue unveränderliche Positions- und Portfolioversion: Cash ändert sich um den exakten Ausführungserlös bzw. die Kosten, Exposure um die Fillmenge zum im Vorschlag referenzierten Markpreis und Equity um beide Deltas. Diese Portfolioversion bleibt `STALE`/`PENDING`; `reconciliation_current=false` und `PAUSE_NEW` verhindern ein Resume mit einem alten Cash-/Equity-Nachweis. Den marktwertbezogenen Equityanteil muss eine spätere autoritative Aktualisierung prüfen.
4. Bei Cancel/Fill-Rennen gewinnt ein definitiver Fill für den gefüllten Anteil. Teilfüllung reduziert die Reservierung proportional; der ungeklärte Rest bleibt reserviert. Erst ein belegter terminaler Status gibt den ungefüllten Rest frei. Ein später Fill nach `CANCELED` erhöht die kumulative Menge und korrigiert Position, Portfolio und Ledger ohne erneute Order. `UNKNOWN` hält die Reservierung. Audit und Outbox werden mit jeder angenommenen Report-/Ledgerwirkung in derselben Transaktion geschrieben; eine Abweichung bekommt einen dauerhaften Fehlerbericht und eine Pause.
5. Keine Order gefunden ist nur dann definitive Nichtannahme, wenn dokumentierte Brokerregeln und eine vollständige Order-/Fillabfrage dies beweisen. Die Fake-Reconciliation behandelt fehlende oder widersprüchliche Evidenz als `UNKNOWN`; sie gibt kein Budget frei. Erst nach konsistentem Ledger, vollständigem Health und aktueller Policyversion kann ein Operator Resume anfordern. **Geplante CLI-Schnittstelle:** `tradingctl resume --if-policy-version <VERSION>`; PTV-12 muss die Gates erneut prüfen.

## DB-, Job- und Stream-Recovery

- DB-Neustart: Execution-Submit lokal sperren; Dienste dürfen keine alten Leases verwenden. Nach Wiederkehr Migrationversion, Constraints, Ledger, Outbox und Audit prüfen, dann Broker-Reconciliation durchführen. Restore startet sicher pausiert; unbekannte Orders verhindern destruktive Downgrades.
- Workerlease: Ein neuer Besitzer erhält einen neuen Fencingtoken. Der alte Besitzer darf keine Ergebnisse committen. Submitjobs werden vor Übernahme reconciliiert und nie als gewöhnlicher Retry behandelt. Nach Retrylimit landet der Job mit redigiertem Fehler im Dead Letter.
- Outbox/SSE: Nach Crash aus dauerhaftem Outboxzustand zustellen und Consumer deduplizieren. Dashboard lädt einen konsistenten Snapshot mit Cursor; bei `410` oder `reset-required` verwirft es den alten Streamzustand und lädt einen neuen Snapshot. Es behauptet keine Kontinuität bei Lücke.

## Abschluss-Evidenz

Recoveryprotokoll enthält Crashphase, Trace-ID, Intent- und brokerseitige Kennungen, letzten definitiven Fill, reserviertes Restbudget, Abfrageumfang, Audit-/Outboxversion und finale SafetyState-Entscheidung. Es enthält keine Credentials oder rohen sensitiven Brokerpayloads. `tests/recovery/test_submit_crash.py` prüft den einmaligen Submit; `tests/recovery/test_reconciliation.py` prüft Fake-Fill- und Restartfälle gegen PostgreSQL. Diese Fake-Nachweise sind keine Zertifizierung eines externen Paper-Adapters. Nach jeder unklaren Bewertung bleiben Order und Budget gehalten und der Operator prüft die konkrete Abweichung anhand der gespeicherten Berichte.
