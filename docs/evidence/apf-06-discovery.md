# APF-06 · Proaktive Researchpipeline

Die Tests verwenden synthetische Evidenz, temporäre PostgreSQL-Datenbanken und ein lokales HTTP-Double. Der Scheduler wird im Test über den Research-Supervisor angesteuert. Die Einbindung in den laufenden Anwendungsdienst und der Browserpfad gehören zu APF-08 und APF-09.

| Anforderung | Nachweis |
| --- | --- |
| APF-D01 | [Scheduler-Tests](../../tests/unit/test_discovery_schedule.py) prüfen Sommerzeitlücke, doppelte Ortszeit und UTC-Ausführung. [PostgreSQL-Recovery](../../tests/recovery/test_discovery_schedule_db.py) prüft Pause, Reaktivierung, konkurrierende Scheduler, Rollback und Restore. |
| APF-D02 | [Trigger-Tests](../../tests/unit/test_discovery_evidence.py) prüfen Frische, Rechte, Qualität, Instrumentbezug und kanonische Deduplikation. [Pipeline-Recovery](../../tests/recovery/test_discovery_runtime.py) prüft identische Evidenz über zwei Zeitpläne und einen vollen Pending-Speicher, bei dem der Cursor vor der nächsten unverarbeiteten Quelle bleibt. |
| APF-D03 | [Publikationstests](../../tests/unit/test_discovery_publication.py) prüfen Zitate, Herkunft, Neuheit, Priorisierung und Limits. Pipeline-Recovery liest die publizierte PROPOSED-Version nach Neustart und verhindert die Veröffentlichung nach einem Lizenzentzug. Experimenttabellen bleiben leer. |
| APF-D04 | Trigger-Tests prüfen reproduzierbare Bindungen und den nur explizit autorisierten Wechsel von Modell oder Prompt. Pipeline-Recovery prüft den unbekannten Zustand nach einem Modellrequest ohne Nutzungsdaten und dessen Deduplikation beim folgenden Zeitplan. |
| APF-Q05 | Die Publikationsprüfung berücksichtigt normalisierte frühere Ideen und persistierte Forschungsevidenz. Pipeline-Recovery verifiziert, dass die veröffentlichte Version referenzierbar ist und keinen Quelltext aus der Modellprojektion enthält. |

Der PostgreSQL-Pipeline-Test erzeugt synthetische Marktrevisionen. Die Migration installiert zusätzlich native Trigger für Qualitätsrevisionen, Quant-Ergebnisse und retrospektive Befunde. Die Datenbanktests provisionieren das Schema bis zur Discovery-Migration und vergleichen die ausgewählten Tabellen nach Dump und Restore.
