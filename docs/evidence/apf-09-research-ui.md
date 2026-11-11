# APF-09 · Material-Researchoberfläche

Der Researchbereich lädt den Companion bei Bedarf. Discovery und Dialog verwenden gespeicherte Ideenfassungen. Advanced zeigt die Bindung an dieselbe Quant-Lab-Definition. Der Browser übernimmt Metriken als Dezimalstrings aus dem Researchbericht und stellt Modelltext als Text dar.

| Anforderung | Nachweis |
| --- | --- |
| APF-C02, APF-Q02, APF-Q04, APF-Q07 | Die [Browserprüfungen](../../tests/e2e/agent-companion.spec.ts) prüfen Tastatur, Fokus, lokale Mehrschritt-Klärung, Versionswechsel, wiederaufgenommene bestätigte Felder und die Bindung der exakten Quant-Lab-Definition in Guided und Advanced. |
| APF-C03, APF-Q03, APF-Q06 | Die Oberfläche zeigt Testbarkeitsgründe, Approximation, Providerfähigkeiten, Registry, Zeitplan, Budget und Laufereignisse. Die Browserprüfungen verifizieren eine separate Quant-Entscheidung mit Freigabeversion und Bindungshash sowie den Reload eines versionierten Berichts mit unverändertem Dezimalwert und Verweis auf den Quant-Batch. |
| APF-C04, APF-C05, APF-P07 | Die Browserprüfungen decken blockierte Vorschau, exakten sichtbaren Ausschnitt, getrennte Übermittlungs- und Hypothesenentscheidungen, Ablehnung, untrusted Modelltext und Sitzungsverlust ab. Die [Research-Vertragstests](../../tests/contracts/test_agent_research_routes.py) prüfen die öffentlichen DTOs und sicheren Fehlerantworten. |
| APF-P07, APF-Q07 | Der [PostgreSQL-Recovery-Test](../../tests/recovery/test_agent_control_http.py) führt dieselben HTTP-Operationen über Control und signierten Relay aus. Der synthetische Provider erhält ausschließlich die freigegebene Projektion. Eine zweite Sitzung kann die Vorschau nicht freigeben und der Wiederholungsaufruf erzeugt keinen zweiten Provideraufruf. |

Die [Companion-](../../tests/integration/test_companion_runtime.py) und [Report-Recovery-Tests](../../tests/recovery/test_research_report_db.py) prüfen Wiederaufnahme, Widerruf, Quant-Admission und persistierte Berichte auf isoliertem PostgreSQL. Der unabhängige Review prüfte Versionsbindung, Widerruf, Eingabegrenzen, Wiederaufnahme und interne Quant-Links. Der Produktionsbuild und die Supervisor-Browserregression wurden zusätzlich ausgeführt.

Die Browserprüfungen arbeiten mit synthetischen Control-API-Antworten. Der PostgreSQL-Recovery-Test prüft Control, Relay und Providergrenze über den echten Backendpfad. Die zusammenhängende Abnahme vom Angular-Browser bis zum quantitativen Worker ist in [APF-10](../../openspec/changes/agent-platform-foundation/work-packages/APF-10.md) vorgesehen.
