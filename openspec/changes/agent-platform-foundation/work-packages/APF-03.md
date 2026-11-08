# APF-03 · Isoliertes LLM-Gateway anbinden

Change: [agent-platform-foundation](../proposal.md)

- **Requirements** · APF-P01, APF-P02, APF-P03, APF-P04, APF-P05, APF-P06, APF-R06, APF-P07, APF-R07.
- **Abhängigkeiten** · APF-01, APF-02.
- **Dateiverantwortung** · `services/llm_gateway/`, `services/setup/`, `infra/compose/`, `tests/security/`. Geteilte Dateien nur nach Vorgängerintegration anfassen.
- **Ziel und Akzeptanz** · OpenAI und Anthropic ausschließlich über offline HTTP-Doubles prüfen. ACL, Egress, Revocation, Permit-Replay und Usage-Unbekannt testen. Produktive Rechte separat.
- **NLP-Refinement** · Nur die versiegelte, sichtbare Projektion unter separater Provider-/Zweck-/Klassenfreigabe senden. Synthetische HTTP-Body-, Secret-/PII-, Klassen- und Widerrufsorakel NLP-02/06/07/08/10/12. Keine Rohprompts dauerhaft ablegen.
- **Sicherheit** · Geringste Rechte, redigiertes Audit, keine realen Providerzugriffe im Harness, keine neuen Broker-, Execution- oder Risk-Rechte.
- **Definition of Done** · Zugeordnete Szenarien und Pakettests bestanden, Befunde behoben, Nachweise versioniert, Checkbox und Issuezustand identisch, Issue und Abhängigkeiten zurückgelesen. Branch und main nach bestehendem Workflow geprüft integriert.

[Abnahmeplan](../test-strategy.md) · [Traceability](../traceability.md)
