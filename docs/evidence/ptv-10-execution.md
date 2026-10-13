# PTV-10: Execution-Submitgate und Paper-Grenze

Stand 2026-10-13. Ausschließlich lokaler FakeBroker; kein externer Brokerzugriff, keine Broker-Credentials und keine echte Order.

## Protokoll

Execution prüft die tatsächliche Datenbankrolle `atl_execution`, ein einziges Konto, unveränderliche Intent-/Proposal-/Riskreferenzen, Kontozuordnung, vollständige aktive Reservierung und aktuelle Versionen. Unter Account-, Kontext- und geordneten Positionssperren liest es die DB-Zeit und prüft TTL, PAPER/RUNNING, Reconciliation, Tagesanker, Policy, Portfolio, Marktevidenz, Session und Fähigkeiten erneut. Die eigene Reservierung wird für die Neubewertung exakt einmal herausgerechnet; andere Positionen, Orders und Reservierungen bleiben wirksam. Nicht zugeordnetes Exposure oder andere SUBMITTING/UNKNOWN-Vorgänge blockieren.

Die erste Transaktion bestätigt SUBMITTING samt stabiler clientOrderId und Audit vor jedem Brokerkontakt. Die zweite Transaktion sperrt erneut, bewertet den aktuellen Stand und hält die Sperren während des synchronen Fake-Aufrufs und Ergebniscommits. Eine dazwischen eintretende Sperre wird vor I/O erkannt. Selbst nach der read-only Capability-Abfrage werden Zeit und sämtliche Gates erneut geprüft. Die bestehenden Policy-/Kontextwriter serialisieren sich mit diesen Sperren. Eine bereits gestartete Anfrage ist in-flight: Pause kann sie nicht rückwirkend verhindern; der Pausewriter wird anschließend wirksam.

Nur der exakte FakeBrokerAdapter und ein ausdrücklich aktivierter lokaler Paper-Smoke-Pfad sind zugelassen; Alpaca wird erst PTV-17 angeschlossen. ACK muss Intent, Client-ID, Trace, Menge und bestätigten Status erfüllen. Unklare Antworten/gewöhnliche Fehler führen zu UNKNOWN, HELD_UNKNOWN, Pause und ausstehender Reconciliation mit stabilem Fehlercode. Ungeprüfte Adapterfehlertexte werden weder auditiert noch als Eventpayload gespeichert. Ein Prozessabbruch nach dem ersten Commit lässt SUBMITTING bestehen; erneute Aufrufe übermitteln niemals blind erneut. Vor I/O definitiv abgelehnte zweite Gates speichern REJECTED und geben das zugehörige Budget auditiert frei.

`ExecutionProcess` setzt bei jeder Initialisierung eine auditierte PAUSE_NEW und invalidiert Reconciliation; HALTED wird erhalten. Ein bereits sicher pausierter Zustand wird nicht unnötig versioniert. Resume-/Health-Orchestrierung und unabhängiger Watchdog bleiben PTV-12; vollständige Broker-Reconciliation/Positionsbuchung bleibt PTV-11. Diese Liefergrenzen erlauben hier keinen automatischen Resume.

## Test- und Review-Evidenz

Erweiterter Sicherheits-/Recovery-Lauf: **16 passed in321.21s**. Ergänzte Capability-/Neustartregression **2 passed in46.25s**, bestehende Risk-Race-Prüfung mit erweitertem Harness **1 passed in24.48s**. Ruff, OpenSpec strict und40REQ/23Tasks-DAG bestanden; Source-Scan243 Dateien/0 Funde. Jeder PG-Fall nutzt eine eigene UUID-Testdatenbank/-Login und readonly/non-root Docker-Testcontainer im internen Netz; Passwörter nur Memory/STDIN, kein Secretmount und kein Broker-Egress. Der anfängliche positive Test scheiterte sicher an einer widersprüchlichen UNKNOWN/UNAVAILABLE-Capability-Fixture; Korrektur betrifft ausschließlich die Fixture, keine Gateabschwächung.

| Invariante | Evidenz |
| --- | --- |
| LIVE, REPLAY, SHADOW, DISABLED und Worker dürfen nicht submitten | Mode-Typ, reale Worker-Datenbankrolle und veränderte persistente Modes; null Submit-/Capabilityaufrufe |
| Expliziter Smoke-Pfad und sichere Neustarts | Disabled-Smoke, Startup-Latch, idempotenter Neustart, HALTED bleibt HALTED |
| Frische Limits und Evidenz | Underreservation, Portfolio-/Policy-/Capabilityversion, Session, Reconciliation und Context-Expiry |
| Gültigkeit unmittelbar vor I/O | Einsekunden-Proposal; Ablauf vor Submit und während langsamer Capability-Abfrage |
| Dauerhafter Zustand vor Wirkung | Getrennte PG-Verbindung sieht SUBMITTING innerhalb des Fake-Aufrufs |
| Kein blinder Retry | Simulierter BaseException-Abbruch vor/nach Fake-Annahme, Timeout nach Annahme, erneute Zustellung mit maximal einem Request |
| Pause serialisiert mit Submit | Pause zwischen den Transaktionen verhindert jeden Kontakt; während Submit blockierter Writer mittels pg_blocking_pids nachgewiesen |
| Budget und Closingmenge | Gültiges LONG sowie FLAT mit eigener bereits gespeicherter Reservierung; Manipulation auf Unterdeckung abgelehnt |
| Unsichere ACK und Redaction | Falsche Client-ID ergibt UNKNOWN; Fehler-Sentinel fehlt in Audit, Reports und Outbox |

Unabhängiger begrenzter Sicherheitsreview von gates.py/intents.py abgeschlossen: keine verbleibenden Codeblocker. Konkrete Befunde zu Decimal-Abzügen, Reservierungsdeckung und TTL während Capability-I/O korrigiert; Laufzeitevidenz vom Hauptagent. Deployment-Egress-/Credentialisolation, OS-Prozessabbruch, vollständige Reconciliation und unabhängiger Kill-Switch sind hier nicht zertifiziert und bleiben den dafür vorgesehenen Tasks zugeordnet.
