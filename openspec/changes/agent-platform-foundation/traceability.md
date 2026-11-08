# Traceability

| Spezifikation | Szenarien und Abnahme | Pakete |
| --- | --- | --- |
| APF-Q01–Q07 | Companion-Szenarien und E2E A/B/D/E, Hypothese, Approximation, Exposure und belegter Bericht | APF-04/05/07/09/10 |
| APF-R01–R07 | Runtime-Szenarien und E2E B/F/G, Fence, Replay, Approval, Budget und Rollback | APF-02/03/05/08/10 |
| APF-P01–P07 | Provider-Szenarien und E2E E/F/G, ACL, Schlüssel, Egress, Injection und Safety | APF-03/04/07/08/10 |
| APF-D01–D04 | Discovery-Szenarien und E2E C/E/G, DST, Deduplikation, Qualitätswechsel und Reanalyse | APF-06/09/10 |
| APF-C01–C05 | Control-Szenarien und E2E A–F, Vertragsprüfung, Browserzustand und Betreiberfreigabe | APF-01/08/09/10 |

UC-01 → A, UC-02 → B, UC-03 → D, UC-04 → C, UC-05 → E, UC-06 → C/D. Die konkreten Szenarien bleiben in den fünf OpenSpec-Deltas. Die Pakete besitzen ihre Komponentennachweise. APF-10 belegt die zusammengesetzte Abnahme.

## NLP-Akzeptanz

| Test | Anforderungen | Pakete |
| --- | --- | --- |
| NLP-01 | APF-Q02, APF-Q07, APF-C05 | APF-04, APF-09, APF-10 |
| NLP-02 | APF-Q02, APF-P07, APF-C05, APF-R07 | APF-03, APF-04, APF-08, APF-09, APF-10 |
| NLP-03 | APF-Q03, APF-Q07 | APF-04, APF-10 |
| NLP-04 | APF-Q02, APF-Q07, APF-C05 | APF-04, APF-08, APF-09, APF-10 |
| NLP-05 | APF-Q02, APF-Q07, APF-R07, APF-C05 | APF-01, APF-02, APF-04, APF-08, APF-09, APF-10 |
| NLP-06 | APF-P04, APF-P05, APF-P07, APF-C04 | APF-03, APF-04, APF-08, APF-10 |
| NLP-07 | APF-P04, APF-P07 | APF-03, APF-08, APF-10 |
| NLP-08 | APF-P04, APF-Q02, APF-C05 | APF-03, APF-04, APF-09, APF-10 |
| NLP-09 | APF-R07, APF-Q02, APF-Q07, APF-C05 | APF-02, APF-04, APF-08, APF-09, APF-10 |
| NLP-10 | APF-P05, APF-Q07, APF-R03 | APF-03, APF-04, APF-10 |
| NLP-11 | APF-Q04, APF-Q07 | APF-04, APF-09, APF-10 |
| NLP-12 | APF-P04, APF-P07, APF-R07, APF-C05 | APF-01, APF-02, APF-03, APF-04, APF-08, APF-09, APF-10 |

APF-01 liefert die Vertragstests. APF-02 sichert den Verbrauch und die Recovery. APF-03 besitzt die Providergrenze, APF-04 den fachlichen Dialog und APF-08 die Privacy-Projektion und API. APF-09 prüft die Material-Oberfläche. APF-10 deckt NLP-01–12 im integrierten Ablauf ab. APF-05, APF-06 und APF-07 behalten ihre bestehenden Zuständigkeiten und Abhängigkeiten.
