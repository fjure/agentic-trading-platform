# Definition of Ready

Planungscheck30.10.2026 nach fachlichem Refinement. Erfüllung erlaubt einen gesonderten Applyauftrag, erteilt ihn jedoch nicht.

| Kriterium | Nachweis |
|---|---|
| Aktueller Repositoryzustand berücksichtigt | discovery.md, Refinementbasis HEAD bdb2ac7, vorbestehende Änderungen ausgeschlossen |
| Architektur und Wiederverwendung entschieden | design.md D1..D7 als sieben changebezogene ADRs mit Alternativen/Begründung; keine zweite Engine |
| Strategiemodelle eindeutig | REQ-QSR-001/002 und D1/D2; konkrete EMA-/Wilder-RSI-Seeds, Ränder, Decimal, Signalzustand |
| Bounded Search/reproduzierbar | D4/D5;32 Suchkandidaten, gewichtete Admission,100000 Barschritte, konkrete Revisionen/Fingerprints |
| Training/Validation/OOS kausal | D3; disjunkte Fachrollen/NOT_TRAINABLE, gebundene risikobegrenzte Policy ohne OOSport, Nichtwahl und rollenübergreifendes Register; research-protocol.md/selection-policy.md |
| Kennzahlen mathematisch definiert | metrics-methodology.md; unabhängige Handorakel, Tagesfrequenz/252/0RF,20Tage, Aktivität/Exposure, exakte selectionValues und N/Agründe |
| Vergleichbarkeit und Datenqualität zwingend | REQ-QCO-001, REQ-QEX-002/006; Fingerprintgruppen, keine Qualitykompensation durch Return |
| Strict/Retrospective sicher erhalten | D2/D4, kein Modusfallback, unveränderte historische Verfügbarkeitsgates und aktuelle Negativgates |
| API/SQL/Runtime nachvollziehbar | D4/D5/D7, additive Migration/geschlossene DTOs, alte Claim-/Hashnegative, echte Apply-Prüfmatrix |
| Issues vollständig synchronisiert | issue-graph.md und github-quant-lab-issue-sync.json;9 offene Issues,18 native Kanten mit Read-back |
| OpenSpec strict bestanden | planning-verification.md |
| Unabhängige Reviews ohne kritische Blocker | planning-review.md, Quant und SQL/Safety getrennt; konkrete P2-Rundungs-/Registerbefunde behoben und nachgeprüft |

Ergebnis: **fachliche und technische Planungs-DoR erfüllt**, sobald der dokumentierte abschließende idempotente Sync und die Veröffentlichung verifiziert sind. Der Abschlussbericht nennt den tatsächlichen Remote-Stand. Keine Anwendungsimplementierung oder neue Runtimezertifizierung. Reale Providerrechte, Marktvalidität und externe Vorexposition bleiben unbewiesen; sie werden nicht durch DoR ersetzt.
