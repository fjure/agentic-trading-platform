# APF-10 · Researchabnahme

Der [Browser-Harness](../../tests/e2e/agent_research_browser.py) startet das gebaute Angular-Dashboard, die Control API mit signiertem Relay, den Research-Supervisor, Quant Control, den nativen Backtest-Worker und frisch migriertes PostgreSQL. Providerantworten stammen aus synthetischen Doubles. Das interne Docker-Netz verwendet keine veröffentlichten Containerports oder echten Provider- und Brokerzugänge.

```powershell
.venv/Scripts/python.exe -m tests.e2e.agent_research_browser
```

Der Lauf benötigt ein gebautes Dashboard, installiertes Chromium und das lokale Research-Testimage. Er prüft exakt drei autorisierte Providerrequests einschließlich des sichtbaren Textausschnitts und der technischen Hülle. PostgreSQL bestätigt genau einen Batch, einen Report und null Brokerorders.

| Szenario | Prüfnachweis |
| --- | --- |
| E2E A, NLP-01/02 | Im Browser liefern zwei deutsche Paraphrasen und ein Gegenbeispiel unterschiedliche oder übereinstimmende Auslöser gemäß Fixture. Der Nutzer bestätigt nur ausgewählte Felder. Die lokale Alternative bleibt nach Ablehnung verfügbar. |
| E2E B | Der Browser führt eine bestätigte EMA-Idee über native Validierung und ausdrückliche Betreiberfreigabe zum Batch und versionierten Report. Die Zahlen und der wiederholbare Reportaufbau werden im [PostgreSQL-Reporttest](../../tests/recovery/test_research_report_db.py) abgeglichen. |
| E2E C | Die [Discovery-Recovery](../../tests/recovery/test_discovery_runtime.py) läuft in derselben Datenbank wie der Browser. Angular zeigt den publizierten Vorschlag. Der [Zeitplantest](../../tests/recovery/test_discovery_schedule_db.py) prüft die native Planung separat. |
| E2E D, NLP-03/04/05/11 | Der Browser prüft lokale Klärung, gebundene Expertendefinition und eine überholte Bestätigung. Die [Quant-Rollenprüfung](../../tests/integration/test_quant_operator_approval.py) ergänzt Feldabweichungen und die separate Approximationsentscheidung. Der [native Quant-Workflow](../../tests/integration/test_quant_lab_acceptance.py) prüft Strict PIT und gekennzeichnete Fensterwiederverwendung. |
| E2E E | Discovery prüft die Retraktion zwischen Request und Publikation. Quant blockiert den Auftrag bei fehlender Kalenderevidenz und bei widerrufenen Datenrechten. |
| E2E F, NLP-06/07/08/10 | Der Browser weist synthetischen PII-Text, einen falschen Bindungshash und eine veraltete Freigabeversion ab. Die [Companion-Runtime](../../tests/integration/test_companion_runtime.py) prüft lokale Klassifikation und getrennte Modellaktionen. Vertrags-, Sicherheits- und Rollenprüfungen ergänzen den Browserpfad. |
| E2E G, NLP-09/12 | Der Browser prüft Sendereceipt, Reload und Admission-Replay. Die [Runtime](../../tests/integration/test_agent_research_runtime.py) und [Companion-Runtime](../../tests/integration/test_companion_runtime.py) prüfen Neustart, Fence, widerrufene Freigaben und unbekannten Providerzustand. |

Der [isolierte Lasttest](../../tests/security/test_quant_lab_isolation.py) maß bei überlappender Quant- und APF-Researchlast PAUSE_NEW mit 0,002 s, Kill mit 0,002 s und Reconciliation mit 0,026 s. Er bestätigte null Brokerorders und drei gefencte Wiederanläufe nach erzwungenem Speicherabbruch.

## Quant-Vorbereitung

Die REVIEW-Transaktion speichert die bestätigte Ideenrevision und den gebundenen Vorbereitungsauftrag gemeinsam. Research prüft Vorlage, Hypothese, Snapshot und Datenrechte über die aktive Lease. Quant Control erstellt und validiert den Entwurf in einer Transaktion mit der bestehenden Quant-Rolle. Der Research-Supervisor speichert die geprüfte Ideenrevision, den Run-Status und die Betreiberfreigabe atomar. Die Aufnahme in die Quant-Warteschlange verwendet die bestehende, getrennte Admission.

Der lokale Datenbankbetreiber kann nach der Migration explizite Tageslimits mit [configure-agent-quant-start.py](../../scripts/configure-agent-quant-start.py) setzen. Er verwendet einen lokalen libpq-Service für den Datenbankeigentümer und übergibt `--service`, `--global-daily` und `--agent-daily`. Wiederholte identische Werte behalten ihre Revision. Änderungen erhöhen die Revision und entwerten ältere Freigabebindungen. Fehlende Policies sperren die Vorbereitung. Ein Limit von null sperrt die Aufnahme. Die Betreiberfreigabe bleibt für jeden einzelnen Start erforderlich.
