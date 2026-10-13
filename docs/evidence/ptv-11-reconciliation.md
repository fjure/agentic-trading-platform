# PTV-11: Reconciliation und wirtschaftliches Orderledger

## Grenze und Protokoll

`reconcile_order` akzeptiert ausschließlich den exakten lokalen FakeBroker und die tatsächliche PostgreSQL-Rolle `atl_execution`. Unter Accountlock prüft es Account-/Intent-/Risk-/Reservation-/Tracezuordnung. Die Abfragen über Client-ID und Broker-ID müssen vollständig denselben Auftrag belegen. Es gibt keinen Submit-, Cancel- oder Retrypfad. Fehlende, widersprüchliche oder unbekannte Evidenz hält das Restbudget, schreibt einen stabilen Fehlerbericht mit Audit/Outbox und setzt beziehungsweise erhält Pause und fehlende Reconciliation. HALTED wird nicht aufgehoben.

Brokerberichte erhalten die lokale dauerhafte Order-ID erst nach Identitätsprüfung. Der Fingerprint vergleicht Status, kumulative Menge, Preis, Gebühr, Ereigniszeit, Trace und Ereignis-ID; interne Zustell-ID, Empfangszeit und numerisch gleiche Decimal-Skalen verändern ihn nicht. Ein wiederholtes Ereignis mit verändertem Inhalt oder eine andere Event-ID mit widersprüchlicher kumulativer Kostenbasis wird quarantänisiert. Freie Quellfehlertexte werden abgelehnt. Ausschließlich für den Fake ohne Gebührenmechanismus bedeutet ausgelassene Gebühr null; positive Gebühren sind hier nicht freigegeben. Externe Adapter benötigen eigene normalisierte Verträge.

Positive, belegte kumulative Fill-Deltas ändern Position und Portfolio genau einmal. Kosten und Erlöse werden aus exakten kumulativen Beträgen abgeleitet; realisierter SELL-PnL aus der bestehenden Kostenbasis. NUMERIC(38,18)-Darstellbarkeit wird vor der Buchung geprüft, mit isolierter Decimal-Präzision80. Nicht exakt darstellbare Preise oder unzureichende Cash-/Exposurebasis werden nicht durch Rundung ersetzt. Alle wirtschaftlichen Schritte werden vorgeprüft und gemeinsam mit Reports, Accountversion, unveränderlichen Positions-/Portfoliosnapshots, Kontextpointer, Reservierung, Audit und Outbox bestätigt. Die unveränderliche Kontozuordnung bleibt erhalten; bestehende Snapshottrigger schreiben die Positionshistorie.

Offener Rest wird proportional reserviert. UNKNOWN gibt nichts frei. Definitive Closure braucht konsistente Order- und Fillbelege einschließlich aller beobachteten Mengen. Ein definitiver später Fill nach Cancel wird als wirtschaftliches Delta gebucht. Historisch terminale Reservierungen verhindern eine Wiederöffnung nach einer widersprüchlichen Antwort, auch über einen zwischenzeitlichen UNKNOWN-Status. Neue Portfolios tragen STALE/PENDING, und Pause bleibt aktiv, bis eine autoritative Gesundheits-/Portfolio-/Reconciliationprüfung den separaten Resume-Pfad freigibt; dieser Kontrollpfad folgt PTV-12.

## Evidenz

Erweiterter isolierter PostgreSQL-Lauf: **15 passed in347.51s**. Explizites ungeordnetes CANCEL_PENDING-/Teilfill-/Cancel-Ack-Rennen: **1 passed in23.23s**. Fingerprint-/Redactionprüfung: **2 passed in0.22s**. Damit18 verschiedene Reconciliation-Prüfungen bestanden. Passende FakeBroker-/Risk-Regressionssuite: **33 passed in0.46s**. Ruff, OpenSpec strict,40REQ/23Tasks-DAG und Source-Scan248/0 bestanden. Testdatei: tests/recovery/test_reconciliation.py; private Gastfixture: tests/recovery/reconciliation_support.py. Jeder PG-Fall besitzt eine eigene UUID-Datenbank und einen nach dem Test entfernten Login, readonly/non-root Testcontainer im internen Netz und ausschließlich synthetischen FakeBroker. Testpasswörter nur Memory/STDIN; keine Broker-Credentials, echte Orders oder Broker-Endpunkte.

| Sicherheitsinvariante | Prüffall |
| --- | --- |
| Positive Deltas genau einmal, konstante Einstandsbasis und Cash | partial, sell, reordered; auch unter Decimal-Präzision6 |
| UNKNOWN hält Risiko, kein blinder Zweit-Submit | timeout, unaccepted mit Disconnect/Reconnect und Submit-Zähler |
| Cancel-/spätere Fillkorrektur ohne doppelte Buchung | cancel-late |
| Ungeordnete Cancelanforderung, Teilfill und Cancel-Bestätigung | cancel-race |
| Idempotente Wiederzustellung aus PostgreSQL | wiederholter Reconcile, duplicate-delivery, Fingerprint-Skalenprüfung |
| Widersprüchliche Kosten oder Brokerkennungen sperren | conflict, cost-amendment, broker-id-mismatch |
| Terminale Order bleibt geschlossen | terminal-regression mit zwei identischen Folgeabfragen |
| Unbepreiste/widersprüchliche Mengen geben kein Budget frei | unknown-price, unpriced-extra-fill |
| Invalides Geldbild rollt vollständig zurück und pausiert dauerhaft | negative-cash |
| Bestätigungsabbruch verwirft alle wirtschaftlichen Wirkungen | rollback; anschließende Wiederholung bucht einmal |
| Freie Fehlertexte landen nicht in Report oder Event | untrusted reason text test |

Unabhängiger begrenzter Quellreview abgeschlossen, keine offenen Befunde; erforderliche Laufzeitszenarien vom Hauptagent bestanden. Korrigiert wurden numerische Hashkanonisierung, vollständige Broker-ID-Belege, historische Kosten-/Terminalprüfung, mengenbezogene Closure-Beweise, Decimal-Rechnung, finanzielle Vorprüfung sowie Policy-Versionen in Fehler-/Kontext-/Portfolio-/Kontoaudits. Dieser lokale Nachweis zertifiziert keinen externen Paper-Adapter, Deployment-Egress oder die noch offene unabhängige Resume-/Watchdog-Orchestrierung.
