# Traceability

Alle Anforderungen besitzen Szenarien im jeweiligen Capabilitydelta; Details stehen in Design/Methodik. Tasks sind Implementierungsarbeit, keine behauptete Umsetzung.

| Requirement | Szenarien | Entscheidung | Paket |
|---|---|---|---|
| REQ-QSR-001 | T-QSR-001/002 | D1/D2 | 01,02,04,08,09 |
| REQ-QSR-002 | T-QSR-003/004/005 | D2 | 02,04,09 |
| REQ-QEX-001 | T-QEX-001 | D4/D5 | 01,02,07,09 |
| REQ-QEX-002 | T-QEX-002 | D2/D4 | 01,02,03,04,07,09 |
| REQ-QEX-003 | T-QEX-003/004 | D3 | 01,05,07,08,09 |
| REQ-QEX-004 | T-QEX-005 | D5 | 01,02,03,07,08,09 |
| REQ-QEX-005 | T-QEX-006 | D4/D5 | 01,03,05,07,09 |
| REQ-QEX-006 | T-QEX-007 | D4/D5/D7 | 03,04,07,08,09 |
| REQ-QCO-001 | T-QCO-001 | D3/D6 | 01,05,06,07,08,09 |
| REQ-QCO-002 | T-QCO-002 | D6/metrics-methodology.md | 01,06,07,08,09 |
| REQ-QCO-003 | T-QCO-003 | D7 | 01,07,08,09 |
| REQ-QCO-004 | T-QCO-004 | D7 | 01,08,09 |
| REQ-QEX-007 | T-QEX-008/009; S1..S12 | D3/selection-policy.md | 01,02,03,05,06,07,08,09 |
| REQ-QEX-008 | T-QEX-010 | D3/research-protocol.md | 01,02,04,05,07,08,09 |
| REQ-QEX-009 | T-QEX-011 | D3/D4/research-protocol.md | 01,03,05,07,08,09 |
| REQ-QCO-005 | T-QCO-005; Methodikorakel Aktivität | D6/metrics-methodology.md | 01,06,07,08,09 |

## Vollständiger Forschungsworkflow

Die zwölf Schritte des verbindlichen Beispiels in research-protocol.md sind abgedeckt:

| Schritte | Requirements | Tasks / Vertrag / Prüfung |
|---|---|---|
| 1 Katalog, 2 neun Kandidaten | QSR-001/002, QEX-001 | 01/02/04/08; GET strategies, Definition-POST; QSR001..005/QEX001 |
| 3 Modus/Daten, 4 vorab Policy, 5 Zeitrollen | QEX-002/003/006/007/008 | 01/02/05/08; geschlossene Definition/Validierung; QEX002/003/007..010 |
| 6 validierte Version starten, 7 einfrieren | QEX-002/004/005/007/009 | 01/03/07; validations/batches, Idempotenz/hash; QEX002/005/006/009/011 |
| 8 gleiche Validation, 9 Auswahl oder Nichtwahl | QEX-002/003/007, QCO-001 | 05/06/07; candidates/results/comparison, selectionValues/Gates; QEX008/009, S1..S12 |
| 10 isoliertes OOS, 11 separater Holdout | QEX-003/007/008/009 | 05/07; Status/Resultate samt Nichtauswertung; QEX003/004/009/010/011 |
| 12 ehrliche Ergebnisdarstellung | QCO-001..005, QEX-008/009 | 01/06/07/08/09; Comparison-/Materialansichten; gesamte QCO-Matrix |

QSL-09 prüft den Workflow mit echter PG/API/Worker/Browserkette und synthetischer Kalender-/Datenfixture; kein neuer Runtimebeleg durch dieses Refinement. APIfelder werden erst in Apply kanonisch ergänzt, der aktuelle HTTPvertrag bleibt unverändert. Dependencygraph bleibt bei neun Paketen und 18 Kanten.

## Abdeckung des Nutzerauftrags

A Register/Strategien ->QSR; B Suche/Snapshot ->QEX001/002/004; C OOS ->QEX003; D Analytics ->QCO002; E Vergleich ->QCO001/004; F Repository ->QEX005; beide Modi und Safety ->QEX006; agententaugliche Verträge ->QCO003. Langfristige zusätzliche Metriken, Märkte und Agenten bleiben ausdrücklich außerhalb v1. Siehe Methodik für konkrete zurückgestellte Kennzahlen.
