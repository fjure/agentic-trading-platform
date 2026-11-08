# APF-05 · Quant-Draft und Approval atomar binden

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-R03, APF-R05, APF-Q03, APF-Q04, APF-Q05.
- **Abhängigkeiten** · APF-02, APF-04.
- **Dateiverantwortung** · `services/control_api/quant_lab_service.py`, `packages/backtesting/experiment_storage.py`, `services/control_api/migrations/versions/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · Bestehende Admission in derselben Transaktion aufrufen. Race, Replay, Teilcommit, manuellen Bypass und unveränderte Expertenwerte prüfen. E2E B/D/F-Backend.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
