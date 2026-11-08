# APF-02 · Researchlauf und Budget absichern

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-R01, APF-R02, APF-R03, APF-R04, APF-R06, APF-R07.
- **Abhängigkeiten** · APF-01.
- **Dateiverantwortung** · `packages/agent_research/`, `services/runtime/agent_research.py`, `services/control_api/migrations/versions/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · Getrennte Claims, Fence, Tooljournal, Register und atomare Limits. PostgreSQL-Crashmatrix und Nebenläufigkeitstests. Keine public.jobs- oder Finanzrechte.
- **NLP-Refinement** · Einmaligen Sendestatus, CAS, Ablauf, Widerruf und Recovery im Researchledger abbilden. NLP-05/09/12 inklusive Crash, fehlendem flüchtigen Payload und parallelem Replay.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
