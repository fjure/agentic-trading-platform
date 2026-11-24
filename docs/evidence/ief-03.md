# IEF-03 · Portfolio Hub

Ausgangsstand `ff8b04a61b13f346cce01e7fed1cf53b5b27fd8c`. Issue-Branch `codex/ptv-109-ief-portfolio-hub`.

`/portfolio` liest die ausdrücklich gewählte Quelle. Fake liest nur den entsprechend konfigurierten lokalen Lab-Ledger. Demo prüft die gespeicherte Run-Eigentümerschaft vor dem Zugriff auf den separaten Demo-Ledger. Alpaca Paper liest gespeicherte Nachweise aus der isolierten Paper-Datenbank. Quelle, tatsächliche Bewertungszeit und Revision bleiben sichtbar. Der erfolgreiche Paper-Abgleich wird für genau die ausgewiesene Finanzrevision überprüft. Der eingefrorene frühere Testfluss bleibt unter `/prototype/portfolio` reproduzierbar.

Die Projektion unterscheidet bestätigtes Cash von unbekannter Finanzbasis sowie einen leeren Bestand von unbekannten Positionen. Die Geldbeträge bleiben Decimalstrings. Belegte Beträge außerhalb USD behalten ihre Originalwährung. Für fehlende Kostenbasis, Gebühren, Bewertungsnachweise oder Historie nennt die API Gründe. Aktuelle Kurse, ungeprüfte Verpflichtungen und fehlende FX-Kurse gehen in keine Finanzberechnung ein. Positionen werden auf Basis eines konsistenten Reads paginiert. Der signierte Cursor bindet Betreiber, Kontext, Run, Limit und vollständige Quellprojektion. Änderung oder Ablauf führt zu 409 mit Neustart ab Seite eins.

### Verifikation

| Szenario | Automatisierter Nachweis |
| --- | --- |
| IEF-T-P01 getrennte Quellen | Authentifizierter ASGI-Vertragstest für drei Quellen und fremde Runs. Isoliertes PostgreSQL mit zwei Demo-Ledgern und getrenntem Paper-Ledger. Chromium-Wechsel mit verzögerter Altantwort. |
| IEF-T-P02 gleichzeitiger Fill | Paper-PostgreSQL liest während einer konkurrierenden Revision weiterhin dieselben gebundenen Positionen. HTTP-Folgecursor lehnt veränderte Positionen auch bei gleicher Cashrevision ab. Akteur-, Kontext- und Limitbindung sowie Manipulation geprüft. |
| IEF-T-P03 Kostenbasis und Gebühren | ASGI und Chromium unterscheiden null, leeres Portfolio und belegte Null. Paper verknüpft Kostenbasis ausschließlich mit derselben Positionsrevision. Unbelegte Nettoergebnisse bleiben unverfügbar. |
| IEF-T-P04 Währung und Historie | Verifiziertes EUR-Beispiel in kanonischer OAS und Schemabundle. ASGI und Chromium erhalten Originalwährung und Decimal-Präzision. Ohne Historie bleiben Rendite und Drawdown unbekannt. |

- `scripts/validate-contracts.py` und `scripts/check-contract-breaking.py --base-ref ff8b04a61b13f346cce01e7fed1cf53b5b27fd8c` bestanden mit oasdiff 1.33.0. `scripts/check-angular-client.ps1 -WriteClient` erzeugte mit OpenAPI Generator 7.26.0 zweimal dieselben 541 Dateien. Die generierten TypeScript-Fixtures kompilierten einschließlich nullfähiger Bewertungszeit.
- `python -m pytest tests/contracts -q` bestand mit 2.496 Fällen. Nach der engeren Eigentumsprüfung der Demo-Präferenzen bestanden 69 fokussierte HTTP-, Unit-, ACL- und Hostingprüfungen. Der Replay nutzt weiterhin den vorhandenen bestätigten Beleg vor jedem Dienstaufruf.
- Angular-Produktionsbuild bestanden. Die bestehende Supervisor-CSS-Budgetwarnung erscheint weiterhin. Alle 23 ausgewählten Chromium-Prüfungen bestanden, einschließlich Portfolio, Onboarding, Prototyp, Barrierefreiheit und mobil bis 320 px.
- Der isolierte Docker/PostgreSQL-Lauf bestand mit 5 Fällen in 75,83 Sekunden. Er deckt die Demo-/Paper-Quellen, IEF-02-Präferenzen, ARP-Provider-Smoke und ARP-Release-Rollback auf dem aktuellen Migrationskopf ab. JUnit-SHA256: `9be31d5ddb6c5aa1e71720cd28826015d2a172730c1c5fd3f854dd1377974e6b`.
- 62 Unit- und Sicherheitsregressionen zu Risk, LIVE-Sperre, Brokerisolation und Reconciliation bestanden.
- Zwei unabhängige Reviews deckten Quell-/Cursorbindung und die Paper-Zugriffsgrenze ab. Der Reviewbefund zur Demo-Auswahl wurde mit der bestehenden Scope- und Replaygrenze korrigiert.

### Berechtigungen und Wiederanlauf

Migration `0061_investment_paper_read` hat nur im Paper-Ledger Wirkung. Sie ergänzt eine lesende Funktion für die bestehende Paper-Control-API-Rolle. Die SQL-Prüfung verlangt die exakte DB-, Login- und Rollenidentität. Nur gebundene Ledgernachweise verlassen den Dienst. Die Projektion hat keine Broker-Credentials, Befehlsoperation oder Reconciliation-Seiteneffekte. LIVE-, Risk-, Budget-, Pause- und Execution-Entscheidungen verwenden weiter ihre bestehenden Pfade.

Bei einem Quellkonflikt wird der Portfolio-Read neu geladen. Neustart der lokalen API beendet vorhandene Sitzungen und Cursor. Ein Rollback auf den vorherigen Anwendungsstand entfernt die neue Oberfläche. Ein Downgrade von 0061 entfernt ausschließlich die additive Lesefunktion. Kein Finanz- oder Auditbestand wird gelöscht. Die integrierte menschliche Prüfung bleibt Teil von IEF-09.

### Integrationskorrektur nach erster Remote-CI

Der Research-Runner bestand 18 von 19 Datenbanktests auf 4250759. Der Paper-Test erwartete einen lokalen Entwicklungstag, während der Workflow ein pro Lauf benanntes Testimage baut. Der Test übernimmt jetzt das veröffentlichte Image des isolierten Runners. Die bestehenden Assertions zur Ledgerrevision und zu den Berechtigungen sind unverändert. Der lokale Nachtest mit exakt dieser Image-Übergabe bestand in 7,17 Sekunden. JUnit-SHA256: `9633202f7f05f908338d18f17a0286cbeade4e78bb03a5f2abd1a2bdd1fc8f5a`.

### GitHub-Integration

[Contracts](https://github.com/fjure/agentic-trading-platform/actions/runs/45413702311) und [Research integration](https://github.com/fjure/agentic-trading-platform/actions/runs/45413702373) bestanden auf `c07a0baa3ada54850bae4a948a8b030bb07f7142`. Der Merge `4c865a647511f402dc1a63d26bddaf1dd30c64f2` übernahm denselben geprüften Git-Baum nach main.
