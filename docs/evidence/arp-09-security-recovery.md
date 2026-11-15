# ARP-09 · Isolation, Recovery und Safety unter Last

Das bestehende Research-Gate führt die Pflichtfälle über `python scripts/test-research-db.py --junitxml research-db.xml` und die Fast-Suite aus `.github/workflows/research-integration.yml` aus. Der Aggregator prüft den Commit-SHA, die Pflichtfall-Manifeste und die redigierte JUnit-Evidenz. Fehlende, übersprungene oder fehlgeschlagene Pflichtfälle blockieren das Gate.

## Produktionskomposition

`python tests/security/research_production_compose.py` rendert die kanonische Produktionskomposition und prüft Dienstgrenzen, Credential- und Journal-Mounts, private Netze, deaktivierten Standard-Egress und fehlende Broker- und Datenbankcredentials im Gateway. Es startet ausgewählte Dienste aus dem gerenderten Modell mit einem isolierten TLS-Emulator und einer erreichbaren Datenbank-Canary auf dem dafür vorgesehenen Netz. Der Test prüft Gateway-UID und Mountrechte, fehlenden Direktzugriff auf Datenbank und Emulator, die Egress-Allowlist, TLS-Zertifikat und SNI, signierten Dispatch, Redirect-Sperre, Credential-Widerruf und Replay über einen Containerneustart. Er erwartet genau zwei synthetische Provider-Aufrufe.

Die separate Testkomposition injiziert die Test-CA, einen synthetischen Egress-Zielresolver und einen Supervisor-Admission-Stub. Die dauerhafte Admission und der Supervisor-Wiederanlauf werden zusätzlich mit PostgreSQL geprüft. Die Testkomposition verwendet synthetische Credentials und keinen echten Provider.

## Recovery und Ausführungsgrenzen

Der PostgreSQL-Fall `tests/recovery/test_arp_dispatch_recovery.py` injiziert Prozessverlust vor und nach dem dauerhaften Send-Commit. Nach Widerruf, Journal-Reopen und Supervisor-Reclaim blockieren Replay, veralteter Fence und Ergebnisübernahme. Nur der möglicherweise versandte Attempt hält Kosten konservativ zurück. Eine spätere Rechnung kann diesen Attempt idempotent und ohne Ergebnispublikation abrechnen. Ein zwischenzeitlicher Tarifwechsel ändert seine gebundene Revision, seine Preise und seine Versandperiode nicht. Der Datenbank-Harness prüft den Backup-/Restore-Rundlauf.

Die Fast-Suite prüft getrennt Journalfehler, doppelte Journal-Claims, genau gebundene Credentialrotation, signierte Abbruchsignale, verspätete Providerantworten, Timeout, Verbindungsabbruch, unbekannte Usage und den gesperrten LIVE-Modus. Die PostgreSQL-Suite prüft parallele Starts und Reservierungen sowie den Experimentstart ausschließlich nach gebundener Betreiberfreigabe. Der Lastfall vergleicht auch die persistierten Paper-Strategieprofile.

## Safety unter Last

Der isolierte Quant-Worker bleibt im Batch `RUNNING`, während ein Research-Worker nach persistiertem Claim abrupt beendet wird. Die gültige alte Lease blockiert einen zweiten Starter. Der reguläre Supervisor übernimmt nach deterministischer Lease-Expiration mit einem neuen Fence. Der alte Claim wird verworfen. Der fortgesetzte Researchlauf hält eine Journaltransaktion und eine bestätigte Gateway-503-Antwort während PAUSE_NEW, Kill und Reconciliation offen. Jede Aktion hat ein festes Zeitbudget von unter fünf Sekunden. Der Test prüft null Brokerorders, ausschließlich lesende Brokeraufrufe und unveränderte geschützte Finanzdaten und Paper-Strategieprofile.

Der bestehende Browsertest für STOP und Kill wartet nach Abbruch auf die vollständige Entfernung des Dialogs. Damit testet der Folgeklick das erneute Öffnen nach dem Abschluss der Material-Dialoganimation.

## Prüfergebnisse

Code-Commit `fce1f94cdae6b1b5a0d0ed258b824d13e88fde48` enthält die geprüften Test- und Gate-Dateien. Lokale Ausführung mit synthetischen Daten:

| Prüfung | Ergebnis |
| --- | --- |
| Research-Fast-Auswahl im netzisolierten Linux-Testcontainer mit Evidenz-Recorder | 175 bestanden |
| Research-Datenbankgate mit Docker und PostgreSQL | 12 bestanden, sämtliche geforderten Fälle im Evidenz-Recorder bestätigt |
| Compose-Prüfung als Einzellauf | Bestanden, zwei erlaubte synthetische Providerrequests |
| Gemessene PAUSE_NEW / Kill / Reconciliation | 0,003 s / 0,002 s / 0,029 s |
| Angular-Produktionsbuild und STOP/KILL-Test | Build bestanden, 5 von 5 Browserwiederholungen bestanden |
| Verträge, Schema und semantischer Diff gegen `32861bf34ef7b4b5cff8f76372598973f669061e` | 2.481 Vertragstests bestanden, davon der oasdiff-Fall nach Bereitstellung des gepinnten Tools einzeln wiederholt. Semantischer Diff ohne Fehler, bekannte allOf-Warnung am unveränderten Readiness-Endpunkt |
| Strict-OpenSpec, Ruff und Credential-Quellscan | Bestanden |

Der GitHub-Read-back von [#104](https://github.com/fjure/agentic-trading-platform/issues/104) bestätigt den Evidenzlink, den unveränderten Titel, die beiden Labels und die abgeschlossenen Abhängigkeiten #99 und #100. Die GitHub-Workflows binden die Wiederholung an den SHA des ausgecheckten Issue-Branches.

Der Windows-Runner erhielt beim ersten Contract-Lauf einen HTTP-403 vom bisherigen Maven-Central-Host. Der Bootstrap lädt denselben OpenAPI-Generator jetzt über den Apache-Maven-Central-Endpunkt. Der Download wurde gegen die bestehende SHA-256-Pin geprüft.
