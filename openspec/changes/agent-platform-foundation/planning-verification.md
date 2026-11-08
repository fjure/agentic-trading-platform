# Planungsreview und Verifikation

Ausgangsbasis: `341edfa724a60a56a2692fe547a30fed44e90f1a`. Drei getrennte Reviewagenten prüften jeweils die Integration außerhalb ihrer eigenen Autorenpakete. Die folgende Tabelle hält ihre Befunde und die Änderungen fest.

| Perspektive | Reviewer | Befund und Planungsauflösung |
| --- | --- | --- |
| Agentic Architecture | Runtime | Native Runtime plausibel. Der Quant-Pfad benötigt dieselbe Datenbank `lab_paper_runtime` und einen transaktionsgebundenen Port. Design legt beides fest. |
| Einsteiger-UX | Quant | Statusfolge, Startsemantik und stabiler Reload waren implizit. API-Design und E2E B machen Freigabe, Start, Moduswechsel und Bindung explizit. |
| Quant Research Integrity | Quant | Qualität und Retraktion zwischen Modellrequest und Veröffentlichung prüfen. Kanonische Gruppen unabhängig von Seiten bilden. Pipeline und E2E C ergänzt. |
| Provider/Credentials | Security | Signiertes Permit allein erkennt keinen aktuellen Fence. Private Zulassung prüft aktuelle Bindung und linearisiert vor Versand. Offline-/Produktivgrenzen und separat begrenzte Probe festgelegt. |
| Prompt Injection und Tool Policy | Security | Unsichere Freitexteingaben vor Speicherung blockieren. Externe Kontexte nur aus lokal extrahierten und zugelassenen typisierten Feldern. Ungestützte Zahlen von publizierten Aussagen ausschließen. |
| Runtime, Recovery und Budget | Runtime | Commitgeordnete Quellenposition, volle Pending-Liste, globales Probe-Budget und bereits angenommene Aktionen bei Sessionverlust explizit definiert. |
| Risk und Execution | Security | Agentengebundene Entwürfe brauchen auch über bestehende Startpfade Approval. Netz, DB-Grants, Rollen und gemeinsame Ressourcen sind E2E-Abnahmekriterien. |

Die kritischen Reviewbefunde sind in den Planungsartefakten adressiert. Betriebsberechtigungen, reale Anbieterbedingungen und APF-Laufzeitverhalten brauchen separate Evidenz. Historische QSL- und Paper-Nachweise sind Baseline. Die im Testplan genannten APF-End-to-End-Tests, kanonischen Vertragsprüfungen und Generatorläufe gehören zur Implementierung.

## Ausgeführte Planungsprüfungen

- Repo-Wrapper OpenSpec 1.14.1, `list --json` und `context --json`. Lokaler Projektroot aufgelöst und Ausgangs-Changes erfasst.
- Lokaler HEAD und Remote-Ausgangsstand mit `git rev-parse HEAD` und `git ls-remote origin HEAD refs/heads/main` geprüft.
- `validate agent-platform-foundation --strict --json` erfolgreich, keine Befunde. `status --change agent-platform-foundation --json` meldet vollständige Planungsartefakte. 5 Deltas mit 26 Requirements und 69 Szenarien. Alle zehn Implementierungstasks bleiben offen.
- GitHub-Read-back siehe [Issue-Graph](issue-graph.md).

## NLP-Refinement am veröffentlichten Stand 9ee5134ca7a0

Lokaler HEAD, Remote-HEAD und Remote-main stimmten vor dem Refinement überein. Die bestehende LocalAuth-Sitzung und Same-Origin-Grenze, der Write-only-Setup-Pfad sowie die getrennten Execution-/Jev-Egressnetze wurden im Code geprüft. Sie sind Bestandsschutz und liefern keine Providerautorisierung.

| Unabhängige Perspektive | Befund und überprüfte Auflösung |
| --- | --- |
| Product und Conversational UX | Verkürzte Folgeantworten brauchen einen Bezug ohne Rohhistorie. Der Dialog bestätigt einzelne Bezugspunkte sichtbar; NLP-04 prüft Ellipse und Rückfrage ohne bestätigten Bezug. |
| LLM-Architektur und Runtime | Ein allgemeiner Idempotenzcache würde die flüchtige Vorschau unterlaufen. Replay ist sitzungsgebunden und flüchtig, Entscheidung liefert nur Receipt. SEND, DECLINE und REVOKE haben getrennte Bindungen. |
| Privacy und Security | Auch formal gültige Modellantworten können sensible Werte enthalten. APF-P05/Q07 sperren Anzeige und Persistenz; NLP-06 prüft einen syntaktisch gültigen Secret-Köder. |
| Quant-Integrität | „Schlechte Quartalszahlen“ dürfen nicht still als Gewinnüberraschung gelten. NLP-01 behandelt dies als unbestätigte Auslegung. Deterministische Testbarkeit und separate Approval bleiben maßgeblich. |

Drei unabhängige Reviewer deckten die vier Perspektiven ab und prüften die jeweiligen Korrekturen erneut. Die Schlussreviews meldeten keine kritischen Planungsbefunde. OpenSpec Strict Validation meldete keine Befunde bei 30 Requirements und 85 Szenarien. NLP-01–12 sind geplante Abnahme; produktive Modellqualität und Safety unter Last sind weiterhin Laufzeitnachweise. Siehe [NLP-Entscheidung](nlp-refinement.md) und [Abnahme](test-strategy.md).
