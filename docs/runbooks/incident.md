# Sicherheits- und Betriebsincident

PTV-21: Im dauerhaften Lab läuft der private Kontrollserver im **Control-API-Container** auf `/run/control/control.sock`, mit Control-Rolle und ohne Anbieterzugang. Die beiden Pause-/Kill-Aufrufe samt Composepräfix stehen in `local-operation.md`. Die darunter gezeigten direkten Python-Aufrufe gelten nur innerhalb des passenden Linux-Containers; der frühere Beispielpfad `/run/trading/control.sock` ist kein aktueller Deploymentpfad. Execution reagiert unabhängig vom Worker/Jev, Watchdog unabhängig von Browser/API-HTTP. Ein beobachteter DB-Ausfall latcht Execution lokal; nach Rückkehr wird PAUSE_NEW dauerhaft gesetzt, und nur eine neue erfolgreiche explizite Recovery kann den Latch lösen. Wiederholte Ticks verändern diese Pausegeneration nicht selbst. UNKNOWN sowie Paper-Reconciliation mit ready=false bleiben nicht verfügbar und halten das Budget.

Docker-Health bedeutet Prozess-/DB-Bereitschaft, keine Markt- oder Orderfreigabe. Default-Fake ohne aktuelle Marktdaten ist erwartbar pausiert. Bei UI-Transportausfall den privaten Containerkontrollpfad verwenden. Der Windows-STDIO-Helfer erweitert keine Dienstnetze; `start-local-lab.ps1 -Stop` beendet nur den eigenen verifizierten Helfer und die vier Rollen. Keine fremden Pythonprozesse oder Docker-Volumes entfernen. Bei Startfehlern nur feste Fehlercodes und Rechte-/Healthbooleans weitergeben; private Dateien und Aktivierungscodes nicht auslesen.

Der lokale Fake-PAPER-Kontrollpfad ist implementiert. Priorität haben Stoppen neuer Risiken, Erhalten des Ledgers und beweisbare Reconciliation. Dieser Nachweis umfasst keinen externen Brokeradapter oder produktiven Betrieb.

## Erkennen und Eingrenzen

| Signal | Sofortaktion | Weiterer Nachweis |
|---|---|---|
| Datenstillstand, falsche Session oder Lookahead | `PAUSE_NEW`; betroffene Instrumente sperren | Letzte abgeschlossene Bar, `availableAt`, Qualitätsflags, Kalender/DST |
| Jev-Timeout, Schemafehler oder fragliche Antwort | ABSTAIN für neue Vorschläge | Entscheidungsversion, Inputhash, Fehlcode; Reconciliation/Pause unabhängig prüfen |
| Broker-Timeout, Rate Limit, UNKNOWN oder Ledgerdrift | `PAUSE_NEW`; Reservierung halten | `clientOrderId`, Broker-/Fillabfrage gemäß `recovery.md` |
| DB-Verlust, Leaseverlust oder Outboxstau | Lokales Execution-Gate sperren; Watchdog prüfen | Persistierte Transaktionen, Fencingtoken, Outboxlag, Auditvollständigkeit |
| Secret-Canary in Log/API/Browser oder unzulässiger Egress | `HALTED`; betroffene lokalen Dienste isolieren | Nur redigierte Artefakte sichern; Secretrotation außerhalb Repo/Prompt |
| UI/SSE-Lücke oder Dashboardausfall | Unabhängigen CLI-/Watchdog-Kontrollpfad nutzen | Backend-Snapshot, Cursor, `410`/`reset-required`, Heartbeats |

## Lokaler Kontrollpfad

Im Linux-Execution-Container mit bereits gestartetem `LocalControlServer` und der privaten Socketdatei ausführen:

```sh
python -m services.execution.control_socket --socket /run/trading/control.sock pause
python -m services.execution.control_socket --socket /run/trading/control.sock kill
python -m services.execution.control_socket --socket /run/trading/control.sock resume --policy-version 1
python -m services.execution.control_socket --socket /run/trading/control.sock activate-paper --policy-version 1
```

Den tatsächlichen Socketpfad und die aktuelle Policyversion einsetzen. Das Verzeichnis muss dem lokalen Dienstbenutzer gehören und Modus 0700, der Socket Modus 0600 besitzen. Server und CLI unterstützen ausschließlich die Linux/Unix-Prozessgrenze; auf nativem Windows bleibt der Pfad geschlossen. Der Containerstart und dauerhafte Dienstbetrieb werden durch diese Tests noch nicht zertifiziert. Der Serverdispatch nutzt eine Control-Rollenverbindung und einen fest zugeordneten Account; Benutzer können weder Account noch Actor noch Healthwerte mitgeben. Jev und Browser werden nicht benötigt.

`pause` bestätigt den persistierten SafetyState und seine Version. `kill` persistiert HALTED; `cancellation=REQUESTED` bedeutet einen ausgeführten best-effort Cancel-Callback, keine bestätigte Orderclosure. Bei `NOT_CONFIGURED` fehlt dieser Callback, bei `UNAVAILABLE` schlug er fehl. In beiden Fällen bleibt HALTED erhalten; bekannte Orders weiter über Execution reconciliieren. Für Cancel muss der Server den Execution-eigenen `cancel_known_after_halt`-Callback ausdrücklich erhalten. Kein Flatten und kein Submit aus dem Kontrollpfad.

`resume` und `activate-paper` liefern ausschließlich REQUESTED mit Request-ID. Das ist keine Handelsfreigabe: Execution muss die Request-ID über `ExecutionProcess.recover` prüfen. Resume gilt nur für bereits aktives PAPER; ein Wechsel aus DISABLED/REPLAY/SHADOW benötigt die getrennte explizite PAPER-Aktivierung. HALTED kann damit nicht aufgehoben werden. Policyversion, aktueller Owner/Fencingtoken, Heartbeat, vollständiges Broker-/Ledgerinventar, alle Instrumentdaten und Portfolio müssen aktuell übereinstimmen. Health allein aktiviert PAPER nicht.

Bei `DENIED`, Timeout oder DB-Verlust keinen erfolgreichen Zustandswechsel annehmen. Audit und Ledger über den autorisierten lokalen Lesepfad prüfen; keine Credentials oder freien Quellfehlertexte kopieren. Eine beobachtete DB-/Ownerstörung sperrt den Execution-Prozess bis zur erfolgreich geprüften Recovery. Watchdog pausiert bei fehlendem, über30 Sekunden altem oder zukünftigem Execution-Heartbeat; wiederkehrende Health hebt die Pause nicht automatisch auf.

## Incidentablauf

1. SafetyState persistent setzen oder bei DB-Ausfall das unabhängige lokale Submitgate geschlossen halten. Watchdog/CLI funktionieren ohne Jev oder Browser. In-flight Requests und Fills bleiben sichtbar; ein Halt kann sie nicht rückwirkend beseitigen.
2. Ursache und Umfang über UTC-Zeitfenster, Trace-IDs, Audit, Health, Datenalter, Jobs, UNKNOWN, Reservierungen und Outbox eingrenzen. Keine Secretwerte oder personenbezogenen Daten kopieren.
3. Bei Broker- oder DB-Unklarheit `recovery.md` ausführen. Keine Reservierung wegen Timeout, fehlender Dashboardanzeige oder einzelner leerer Brokerabfrage freigeben.
4. Bei Secretverdacht betroffene Keys außerhalb des Repositorys nach lokalem Secretverfahren rotieren, Prozesse mit neuen restriktiven Mounts starten und Redaction-/Egressprüfungen wiederholen. Keine Keys in Ticket, Prompt, Screenshot oder Log aufnehmen.
5. Vor Resume: Ursache behoben, Daten frisch und plausibel, Health aktuell, alle Jobs/Outboxzustände bekannt, Broker-/Ledger-Reconciliation konsistent, Policyversion unverändert oder neu geprüft, Audit vollständig. Operator fordert Resume explizit an; Backend entscheidet.

## Nacharbeit

Ein redigierter Incidentbericht hält Auslöser, betroffene Requirement-IDs, UTC-Timeline, SafetyState-Übergänge, UNKNOWN-/Reservation-Zahlen, Reconciliationbeleg, Testreproduktion und verbleibende Unsicherheit fest. Bei unbeweisbarem Status bleibt der Fall offen und PAPER pausiert. Neue Failure-Fixtures ergänzen die Recovery-/Security-Suite vor Freigabe.
