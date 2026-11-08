# ADR 01 · Orchestrierung

Status: vorgeschlagen für v1.

| Kriterium | Bestehende Python/PostgreSQL-Muster | LangGraph | Pydantic AI |
| --- | --- | --- | --- |
| Persistenz, Recovery | Ein führender Zustand, Lease und Fence nach vorhandenem Muster. APF ergänzt Versuchsjournal und Queuegrenze. | Checkpoints und Resume. Knoten können von vorne laufen. | Typisierte Agenten und Deferred Tools. Durable Execution erfolgt über Integrationen. |
| Tool Calling, Human-in-the-loop | Geschlossenes Register und SQL-gebundene Betreiberfreigabe. | Graphknoten und Interrupts. Authentifizierung und bindende Freigabe bleiben lokale Pflichten. | Typisierte Tools und zurückgestellte Freigaben. Quant-Admission muss weiterhin lokal transaktional sein. |
| Fencing, Idempotenz | Bestehendes Transaktionsmuster und eindeutige Aktionsschlüssel. | Domänenfence und idempotente Seiteneffekte zusätzlich nötig. | Domänenfence und idempotente Seiteneffekte zusätzlich nötig. |
| Provider und Isolation | Schmale Adapter und getrenntes Gateway. | Integrationen möglich. Prozess-/Netz- und Credentialgrenzen separat. | Provideradapter verfügbar. Das Gateway und seine Grenzen bleiben separat. |
| Debugging und Tests | Vorhandene SQL/HTTP-Harnesses, Zustands- und Crashorakel, schlanke Auditspur. | Graphzustand und Checkpoints helfen beim Debugging. Zusätzliche Replaytests und Zustandsprojektion. | Agentendoubles helfen bei typisierten Outputs. Integrations-Recovery braucht einen eigenen Harness. |
| Ressourcen, Betrieb und Integration | Zusätzliche Research-Runtime und Gateway, gemeinsame vorhandene Datenbank. | Zusätzlicher Checkpoint-Lebenszyklus und Bibliotheksbetrieb auch bei lokalem Betrieb. | Leicht als Adapterbibliothek. Separate Durable-Integration erhöht Betriebs- und Testfläche. |

Entscheidung: Python/PostgreSQL mit geschlossenem Supervisor. Die v1-Schrittabfolge ist fest, die anspruchsvollen Garantien liegen in Admission, Budget, Fence und Evidenz. Ein Framework würde diese Garantien nicht übernehmen. Wiedervorlage bei nachgewiesenem Bedarf an mehreren kooperierenden Agenten oder dynamisch verzweigten Workflows. Keine Leistungs- oder Kostenbenchmarks ausgeführt. Bewertung anhand der dokumentierten Semantik und des lokalen Bestands.

Quellen: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [Pydantic AI Durable Execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/), [Pydantic AI Deferred Tools](https://ai.pydantic.dev/deferred-tools/). Dokumentation über Context7 abgerufen und ergänzend offizielle Seiten geprüft.
