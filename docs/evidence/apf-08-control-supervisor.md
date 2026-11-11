# APF-08 · Control API und Supervisor

Die Control API registriert alle 30 vorhandenen Agent-Operationen. Die Standalone-Schemas übernehmen die für die Laufzeit benötigten Komponenten aus dem kanonischen OpenAPI-Vertrag. Die Vertragstests prüfen die Übereinstimmung von Pfaden, Methoden und DTOs.

| Anforderung | Nachweis |
| --- | --- |
| APF-C01, C03, C04 | [HTTP-Vertragstests](../../tests/contracts/test_agent_research_routes.py) und [PostgreSQL-Recovery](../../tests/recovery/test_agent_control_http.py) prüfen Session, Origin, Wiederholung, Versionskonflikte, paginierte und redigierte Projektionen, Diagnose, Abbruch und Zeitplan. |
| APF-C05, P04, P07, Q07, R07 | PostgreSQL-Recovery führt eine synthetische Interpretation über Control und den signierten Research-Relay aus. Eine zweite Sitzung kann die Vorschau nicht freigeben. Das HTTP-Double empfängt genau die freigegebene Projektion. Die fachliche Bestätigung erfolgt in einer separaten HTTP-Aktion. Der [Companion-Recovery-Test](../../tests/integration/test_companion_runtime.py) prüft unter anderem lokale Klärung, verlorene Vorschau, Rechtewechsel, Widerruf und gebundene Ergebnispublikation. |
| APF-R02 | Der Research-Supervisor beansprucht Research-Läufe und führt nach terminalem Quant-Batch die fällige Berichtserstellung aus. [Quant- und Report-Recovery](../../tests/recovery/test_research_report_db.py) prüft die getrennte Betreiber-Admission, den nativen Quant-Worker, die erneute Einreihung, die aktuelle Lease und den versionierten Bericht nach Restore. |
| APF-P02 | [Provider-Tests](../../tests/security/test_agent_provider_controls.py) prüfen Einrichtung, Probe, Aktivierung, Rotation, Widerruf und redigierte Statusdaten mit dem synthetischen Einrichtungsadapter. Dieser Adapter akzeptiert ausschließlich den Testwert. Die Einrichtungsoperationen sind vom [Research-Relay](../../tests/security/test_agent_research_relay.py) ausgeschlossen. |

## Laufzeit und Isolation

Das optionale [Compose-Profil](../../infra/compose/compose.research-control.yaml) verbindet Control mit Research und Quant-Admission über zwei interne RPC-Netze. Beide Dienste haben jeweils ein separates internes PostgreSQL-Netz und eigene Rollen. Der Quant-Start läuft vom authentifizierten Control-Endpunkt zur privaten Quant-Admission. Die Research-Rolle kann die Admission-Funktion nicht ausführen. Die Schlüssel für die beiden RPC-Grenzen werden getrennt bereitgestellt.

Der Bootstrap erwartet die fest benannten, bereits provisionierten Paper-Login-Identitäten und private Schlüsseldateien. Der Zeitplan wird als deaktiviert mit null Modellaufrufen und Publikationen angelegt. Die synthetischen Providerprofile und der Einrichtungsadapter werden explizit im Offline-Harness eingebunden. Der Browser-Produktpfad für Guided und Advanced folgt in APF-09.

Die [Deployment-Prüfungen](../../tests/security/test_paper_deployment.py) bestätigen die disjunkten Netze und Secret-Mounts sowie die fest gebundenen HBA-Identitäten. `docker compose config` bestätigt die zusammengeführte Topologie ohne veröffentlichte Ports der beiden privaten Dienste. Die explizit geprüften Alembic-Heads behalten das [exakte Paper-ACL-Manifest](../../tests/unit/test_paper_privilege_manifest.py). Der [isolierte Quant-Lasttest](../../tests/security/test_quant_lab_isolation.py) prüft den Worker ohne Provider-Mounts, `PAUSE_NEW`, Kill und Reconciliation unter Quant-Last, den unveränderten Handelsledger und null Brokerorders mit synthetischen Komponenten. Zwei zusätzliche Prüfungen setzen ein separat provisioniertes Paper-Deployment voraus.
