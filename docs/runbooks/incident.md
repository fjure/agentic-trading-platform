# Sicherheits- und Betriebsincident (geplant)

Dieses Runbook beschreibt die Reaktion auf Daten-, Broker-, Decision-, DB-, Secret- und Kontrollstörungen der späteren lokalen Anwendung. Derzeit gibt es keine Runtime. Priorität haben Stoppen neuer Risiken, Erhalten des Ledgers und beweisbare Reconciliation; ein Incident ist keine Freigabe für einen direkten Brokerorder-Workaround.

## Erkennen und Eingrenzen

| Signal | Sofortaktion | Weiterer Nachweis |
|---|---|---|
| Datenstillstand, falsche Session oder Lookahead | `PAUSE_NEW`; betroffene Instrumente sperren | Letzte abgeschlossene Bar, `availableAt`, Qualitätsflags, Kalender/DST |
| Jev-Timeout, Schemafehler oder fragliche Antwort | ABSTAIN für neue Vorschläge | Entscheidungsversion, Inputhash, Fehlcode; Reconciliation/Pause unabhängig prüfen |
| Broker-Timeout, Rate Limit, UNKNOWN oder Ledgerdrift | `PAUSE_NEW`; Reservierung halten | `clientOrderId`, Broker-/Fillabfrage gemäß `recovery.md` |
| DB-Verlust, Leaseverlust oder Outboxstau | Lokales Execution-Gate sperren; Watchdog prüfen | Persistierte Transaktionen, Fencingtoken, Outboxlag, Auditvollständigkeit |
| Secret-Canary in Log/API/Browser oder unzulässiger Egress | `HALTED`; betroffene lokalen Dienste isolieren | Nur redigierte Artefakte sichern; Secretrotation außerhalb Repo/Prompt |
| UI/SSE-Lücke oder Dashboardausfall | Unabhängigen CLI-/Watchdog-Kontrollpfad nutzen | Backend-Snapshot, Cursor, `410`/`reset-required`, Heartbeats |

**Geplante CLI-Schnittstellen, derzeit nicht ausführbar:** `tradingctl pause --reason <CODE>`, `tradingctl halt --reason <CODE>` und `tradingctl status --redacted`. Diese Namen sind Entwurfsplatzhalter; der spätere Apply muss den tatsächlichen Vertrag und die Tests festlegen. Kein Befehl in diesem Dokument darf vor Runtime-Zertifizierung als Ordersteuerung benutzt werden.

## Incidentablauf

1. SafetyState persistent setzen oder bei DB-Ausfall das unabhängige lokale Submitgate geschlossen halten. Watchdog/CLI funktionieren ohne Jev oder Browser. In-flight Requests und Fills bleiben sichtbar; ein Halt kann sie nicht rückwirkend beseitigen.
2. Ursache und Umfang über UTC-Zeitfenster, Trace-IDs, Audit, Health, Datenalter, Jobs, UNKNOWN, Reservierungen und Outbox eingrenzen. Keine Secretwerte oder personenbezogenen Daten kopieren.
3. Bei Broker- oder DB-Unklarheit `recovery.md` ausführen. Keine Reservierung wegen Timeout, fehlender Dashboardanzeige oder einzelner leerer Brokerabfrage freigeben.
4. Bei Secretverdacht betroffene Keys außerhalb des Repositorys nach lokalem Secretverfahren rotieren, Prozesse mit neuen restriktiven Mounts starten und Redaction-/Egressprüfungen wiederholen. Keine Keys in Ticket, Prompt, Screenshot oder Log aufnehmen.
5. Vor Resume: Ursache behoben, Daten frisch und plausibel, Health aktuell, alle Jobs/Outboxzustände bekannt, Broker-/Ledger-Reconciliation konsistent, Policyversion unverändert oder neu geprüft, Audit vollständig. Operator fordert Resume explizit an; Backend entscheidet.

## Nacharbeit

Ein redigierter Incidentbericht hält Auslöser, betroffene Requirement-IDs, UTC-Timeline, SafetyState-Übergänge, UNKNOWN-/Reservation-Zahlen, Reconciliationbeleg, Testreproduktion und verbleibende Unsicherheit fest. Bei unbeweisbarem Status bleibt der Fall offen und PAPER pausiert. Neue Failure-Fixtures ergänzen die Recovery-/Security-Suite vor Freigabe.
