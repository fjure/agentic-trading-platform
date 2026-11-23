# Research-Provider deaktivieren und zurückrollen

Für die Installation den geprüften Quellstand und die Reihenfolge der Overlays aus dem [Research-Betreiberablauf](../../openspec/changes/agent-research-production-readiness/operator-runbook.md) verwenden. Bei der ersten Inbetriebnahme die Migrationen vorwärts ausführen. Im Dashboard die gesperrte Provider-Bereitschaft, den deaktivierten Zeitplan und eine lokale Rückfrage ohne Providerfreigabe bestätigen.

## Abschalten

1. Im Research Companion den aktiven Zeitplan pausieren. Bestätigen, dass **PAUSED** mit einer neuen Version angezeigt wird. Stand der letzten Ausführung und vorhandene Reports bleiben als Evidenz erhalten.
2. Im lokalen Research-Setup unter Beibehaltung des laufenden Supervisors einen Einmal-Grant für `revoke` anfordern und damit die aktuelle Credential-Generation widerrufen. Die Setup-Route sperrt zuerst die Supervisor-Autorität und widerruft alle Providerbindungen, bevor der lokale Speicher geändert wird. Bei einer unklaren Antwort den redigierten Status prüfen. Anschließend den Schlüssel beim Anbieter widerrufen.
3. Im selben Compose-Projekt die optionalen Gateway- und Egress-Dienste mit `docker compose … stop research-llm-gateway research-provider-egress` stoppen. `…` steht für die zuvor verwendeten Projekt-, Profil- und Dateiparameter. Die Sperre erfolgt vor dem Stopp. Die installierte Dispatch-Konfiguration und die Egress-Freigabe beim Umschalten entfernen.
4. Den Supervisor mit der geprüften Research-Control-Konfiguration ohne Dispatch-Overlay und ohne installiertes Providerprofil wieder starten. Datenbank, Audit und Gateway-Journal auf demselben dauerhaften Stand belassen. Der App-Rollback verwendet das vorwärts migrierte Schema. Das Datenbank-Upgrade bleibt bestehen.
5. Nach dem Neustart bestätigen, dass der Providerstatus **DISABLED** anzeigt, der Zeitplan pausiert bleibt und ein neuer Research-Dialog eine lokale Rückfrage anzeigt. Alte Vorschauen, Profilgenerationen und Schedule-Epochen bleiben gesperrt. Möglicherweise bereits versandte Attempts bis zur Abrechnung konservativ reservieren. Bestehende Report- und Audit-Evidenz anhand ihrer gebundenen IDs prüfen.

Für eine erneute Inbetriebnahme eine neue Credential- und Profilbindung, aktuelle Tarif- und Rechtebelege und einen neuen Betreiberentscheid verwenden. Journal und Ledger sichern und ihre Identität vor Wiederaufnahme des Dispatch prüfen.

## Offline-Prüfung

`python scripts/test-research-db.py --junitxml research-db.xml` startet eine disposable PostgreSQL-Umgebung. Der Rollback-Fall verwendet die echte Datenbankmigration, Zeitplanpersistenz, signierte Credential-Widerrufsgrenze, das Gateway-Journal und den lokalen HTTP-Dialog. Als Gegenstelle dient ein synthetischer Transport. Der Fall vergleicht Fingerprints der Finanzdaten, die Provider-Ledgerzeilen und die Run-Ereignisse. Der PostgreSQL-Harness führt einen zusätzlichen Backup-/Restore-Vergleich aus.
