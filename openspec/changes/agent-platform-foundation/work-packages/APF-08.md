# APF-08 · Control API und Supervisor integrieren

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-C01, APF-C03, APF-C04, APF-R02, APF-P02.
- **Abhängigkeiten** · APF-05, APF-06, APF-07.
- **Dateiverantwortung** · `services/control_api/agent_research_routes.py`, `services/control_api/app.py`, `services/control_api/research_client.py`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · Private Relays und Auth integrieren. Vertrags-/Session-/CSRF-/Idempotenztests auf realem HTTP-Pfad, redigierte Statusprojektionen.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
