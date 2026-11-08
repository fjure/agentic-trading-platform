# APF-01 · Additiven HTTP-Vertrag festlegen

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-C01, APF-C04, APF-P02, APF-R05, APF-C05, APF-P07, APF-Q07, APF-R07.
- **Abhängigkeiten** · keine.
- **Dateiverantwortung** · `contracts/http/control-api.openapi.yaml`, `contracts/schemas/`, `contracts/examples/`, `apps/dashboard/src/app/api/generated/control-api/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · Typisierte APF-DTOs und Fehler mit kompatiblem Vertrag. Strict Lint, Beispiele, Breaking-Diff, Generator-Drift und Auth-/Replay-Negative.
- **NLP-Refinement** · Geschlossene Preview-, ProcessingDecision-, Receipt- und Hypothesenverträge mit no-store, Versionsbindung und sicheren Fehlerprojektionen spezifizieren. NLP-05/12 als Vertragsorakel.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
