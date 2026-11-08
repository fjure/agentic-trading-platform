# APF-04 · Ideen und Dialog versionieren

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-Q01, APF-Q02, APF-Q03, APF-Q05, APF-P04, APF-P05.
- **Abhängigkeiten** · APF-01, APF-02, APF-03.
- **Dateiverantwortung** · `packages/agent_research/companion/`, `tests/unit/`, `tests/integration/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · Original und Interpretation trennen, bestätigte Annahmen binden, Testbarkeit und Exposition prüfen. E2E A/E-Backendkette und Injection-Negative.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
