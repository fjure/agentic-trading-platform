# Traceability

| Paket | Requirements | Abhängigkeiten | Evidenz |
| --- | --- | --- | --- |
| [ARP-01](work-packages/ARP-01.md) | ARP-I02, ARP-K01, ARP-P01  | — | Vertragsbericht und Compose-Topologie |
| [ARP-02](work-packages/ARP-02.md) | ARP-P02  | ARP-01 | Wire- und Netzisolationsbericht |
| [ARP-03](work-packages/ARP-03.md) | ARP-K01, ARP-K02, ARP-P01, ARP-P06  | ARP-01 | Synthetischer Credential- und Mountnachweis |
| [ARP-04](work-packages/ARP-04.md) | ARP-K02, ARP-P01, ARP-P02, ARP-P03, ARP-P04, ARP-P05, ARP-P06  | ARP-02, ARP-03 | PostgreSQL- und Race-Evidenz |
| [ARP-05](work-packages/ARP-05.md) | ARP-I01  | ARP-01 | GitHub-Run und JUnit |
| [ARP-06](work-packages/ARP-06.md) | ARP-E01, ARP-E02  | ARP-01 | Goldstandard-Review und Scorer-Bericht |
| [ARP-07](work-packages/ARP-07.md) | ARP-K01, ARP-P02, ARP-P03, ARP-P05, ARP-P06, ARP-P07  | ARP-04, ARP-08 | Browsernachweis und Betreiber-Runbook |
| [ARP-08](work-packages/ARP-08.md) | ARP-P04, ARP-P06  | ARP-04 | Redigierter Offline-Smoke-Nachweis |
| [ARP-09](work-packages/ARP-09.md) | ARP-I02, ARP-K01, ARP-K02, ARP-P02, ARP-P04, ARP-P06  | ARP-04, ARP-05 | Isolations-, Last- und Recovery-Bericht |
| [ARP-10](work-packages/ARP-10.md) | ARP-E01, ARP-E02, ARP-I01, ARP-I02, ARP-P06, ARP-P07  | ARP-05, ARP-06, ARP-07, ARP-08, ARP-09 | Readiness-Matrix mit getrenntem Betriebsnachweis |

## Normative Szenario- und Paketzuordnung

- `ARP-P01-S01` Gesperrter Neustart → [ARP-01](work-packages/ARP-01.md), [ARP-04](work-packages/ARP-04.md)
- `ARP-P02-S01` Gebundener Dispatch → [ARP-02](work-packages/ARP-02.md), [ARP-04](work-packages/ARP-04.md)
- `ARP-P02-S02` Ungültiger Auftrag oder Ziel → [ARP-02](work-packages/ARP-02.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P02-S03` Abweichende Antwortidentität → [ARP-02](work-packages/ARP-02.md)
- `ARP-P02-S04` Provider drosselt → [ARP-02](work-packages/ARP-02.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P03-S01` Geänderte Versandvorschau → [ARP-04](work-packages/ARP-04.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P03-S02` Unabhängige Quantentscheidungen → [ARP-04](work-packages/ARP-04.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P03-S03` Bewusster Moduswechsel → [ARP-04](work-packages/ARP-04.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P04-S01` Konkurrierende Reservierungen → [ARP-04](work-packages/ARP-04.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P04-S02` Timeout oder fehlende Usage → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P04-S03` Unvollständiger oder veralteter Tarif → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md)
- `ARP-P04-S04` Budgetrevision oder UTC-Wechsel → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md)
- `ARP-P04-S05` Belegte Kostenüberschreitung → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P05-S01` Widerrufene Discovery-Rechte → [ARP-04](work-packages/ARP-04.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P05-S02` Interaktive Übernahme → [ARP-04](work-packages/ARP-04.md), [ARP-07](work-packages/ARP-07.md)
- `ARP-P06-S01` Günstiges und teureres Modell → [ARP-08](work-packages/ARP-08.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-P06-S02` Vorbereitete Bindung und fehlende Freigabe → [ARP-08](work-packages/ARP-08.md)
- `ARP-P06-S03` Fehlende obere Schranke → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md)
- `ARP-P06-S04` Reasoning und Cachekomponenten → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md)
- `ARP-P06-S05` Limit unter Maximalbetrag → [ARP-07](work-packages/ARP-07.md), [ARP-08](work-packages/ARP-08.md)
- `ARP-P06-S06` Veralteter oder wiederholter Attempt → [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P06-S07` Entzug vor oder während Probe → [ARP-03](work-packages/ARP-03.md), [ARP-04](work-packages/ARP-04.md), [ARP-08](work-packages/ARP-08.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-P06-S08` Antwort ohne verifizierbare Verbindung → [ARP-08](work-packages/ARP-08.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-P07-S01` Diagnose mit lokaler Alternative → [ARP-07](work-packages/ARP-07.md)
- `ARP-K01-S01` Einmalige Schlüsseleingabe → [ARP-03](work-packages/ARP-03.md)
- `ARP-K01-S02` Ungültiger Setup-Request → [ARP-03](work-packages/ARP-03.md)
- `ARP-K01-S03` Prozesszugriffsgrenze → [ARP-03](work-packages/ARP-03.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-K02-S01` Rotation entwertet alte Generation → [ARP-03](work-packages/ARP-03.md), [ARP-04](work-packages/ARP-04.md)
- `ARP-K02-S02` Widerruf vor Commit und verspätetes Ergebnis → [ARP-03](work-packages/ARP-03.md), [ARP-04](work-packages/ARP-04.md), [ARP-09](work-packages/ARP-09.md)
- `ARP-I01-S01` Vollständiger Pflichtlauf → [ARP-05](work-packages/ARP-05.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-I01-S02` Fehlende Umgebung oder Skip → [ARP-05](work-packages/ARP-05.md)
- `ARP-I01-S03` Unvollständiges Manifest-Ergebnis → [ARP-05](work-packages/ARP-05.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-I02-S01` Recovery und Isolation unter Last → [ARP-09](work-packages/ARP-09.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-I02-S02` Verletzte Sicherheitsgrenze → [ARP-09](work-packages/ARP-09.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-E01-S01` Unabhängige Referenzrevision → [ARP-06](work-packages/ARP-06.md)
- `ARP-E01-S02` Unzureichende Pflichtabdeckung → [ARP-06](work-packages/ARP-06.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-E02-S01` Gültige und mehrdeutige Interpretation → [ARP-06](work-packages/ARP-06.md)
- `ARP-E02-S02` Fehlende Modellantwort → [ARP-06](work-packages/ARP-06.md)
- `ARP-E02-S03` Erfundene oder veränderte Angaben → [ARP-06](work-packages/ARP-06.md)
- `ARP-E02-S04` Kritischer Fehler trotz hoher Scores → [ARP-06](work-packages/ARP-06.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-E02-S05` Paarweise Regression → [ARP-06](work-packages/ARP-06.md), [ARP-10](work-packages/ARP-10.md)
- `ARP-E02-S06` Erste modellgebundene Freigabe → [ARP-06](work-packages/ARP-06.md), [ARP-10](work-packages/ARP-10.md)
