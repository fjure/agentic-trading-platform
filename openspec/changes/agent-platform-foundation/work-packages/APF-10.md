# APF-10 · Zusammenhängende Researchabnahme durchführen

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-C01, APF-C02, APF-C03, APF-C04, APF-D01, APF-D02, APF-D03, APF-D04, APF-P01, APF-P02, APF-P03, APF-P04, APF-P05, APF-P06, APF-Q01, APF-Q02, APF-Q03, APF-Q04, APF-Q05, APF-Q06, APF-R01, APF-R02, APF-R03, APF-R04, APF-R05, APF-R06, APF-C05, APF-P07, APF-Q07, APF-R07.
- **Abhängigkeiten** · APF-09.
- **Dateiverantwortung** · `tests/e2e/`, `tests/security/`, `docs/evidence/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · E2E A–G im integrierten lokalen Stack, Contract-Drift, Risk-/Execution-Isolation und PAUSE_NEW/Reconciliation unter APF-Last nachweisen.
- **NLP-Refinement** · NLP-01–12 im integrierten Stack nachweisen. HTTP-Doubles prüfen Protokoll und Autoritätsgrenzen. Eine produktive Modellprofilzulassung erfordert gesondert autorisierte deutsche Semantik-Replays.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
