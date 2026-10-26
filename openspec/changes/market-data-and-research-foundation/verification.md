# Verifikation und Übergabe

## Aktueller Applystand 26.10.2026

**Apply abgeschlossen:9/9 Tasks.** Die Researchimplementierung ist unabhängig geprüft
und mit neun getrennten Issuecommits auf Remote-main integriert. Tatsächliche
Unit-/Contract-/Security-, PostgreSQL-, Safety- und Browserläufe samt Grenzen stehen
im [Implementierungsnachweis](../../../docs/evidence/mdr-implementation-verification.md).
Alle16Requirements/31Szenarien sind dort zugeordnet. Issues42–50 sind geschlossen und
vollständig zurückgelesen. SHA-/Integrationsnachweise stehen in
`docs/development/github-research-git-integrations.json`. Drei Main-Specs synchronisiert;
OpenSpec strikt15/15. Persönliche Providerrechte bleiben UNVERIFIED.
Die folgende Planungsverifikation ist historisch und beschreibt keinen aktuellen
Implementierungsfehlstand.

## Historischer Planungsstand

Stand 25.10.2026. **Planungsartefakte 4/4 vollständig, Implementierung 0/9.** Kein Researchfeature als implementiert oder produktiv zertifiziert ausgeben. Kein neuer Branch/Commit/Push, keine Archivierung und keine Brokerorder in diesem Durchlauf.

## Ausgangszustand und persistenter Kontext

Code-/Test-/Architekturprüfung: [Bestandsaufnahme](../../../docs/evidence/market-research-discovery.md). Paper-, Risiko-/Execution- und Jev-Shadowfunktionen sind vorhanden; generische historische Ingestion, Vintages, ResearchEvidence-/MarketContext-API, Backtesting und Multi-Agent-Orchestrierung fehlen.

Produktvision, Architekturprinzipien und Roadmap liegen kanonisch in `docs/vision/`; AGENTS und OpenSpec-Kontext referenzieren sie. Autonomiestufen entsprechen dem Nutzerauftrag:0Research,1Empfehlungen mit menschlicher Freigabe,2kontrolliertes automatisches Paper,3später ausdrücklich freigegebenes begrenztes LIVE,4langfristige erweiterte Autonomie. Aktuell bleibt LIVE technisch unerreichbar.

## Spec-Konsolidierung

Alle acht bisherigen Capability-Deltas in Main-Specs synchronisiert:61Requirements/82Szenarien. Vollständige Requirementblöcke unverändert erhalten und per Hash verglichen; [Quellhashes und Prüfnachweis](../../../docs/evidence/research-spec-consolidation.md). Bestehende Changes nicht blind archiviert. APW-Verifikation mit aktuellem Statusvorspann von historischen Planungs-/Fehlerständen abgegrenzt. Frühere Abnahmeevidenz wird nicht als neu ausgeführter Lauf dargestellt; tatsächliche menschliche Alpaca-Abnahme bleibt unverifiziert.

## Neuer Change und Entscheidungen

Proposal, drei fachliche Deltas, Design, neun abhängigkeitsgeordnete Tasks und [Teststrategie](test-strategy.md) vorhanden.16Requirements/28Szenarien vollständig den Tasks und geplanten Prüfpfaden zugeordnet. Die vorgesehenen neuen Tests sind noch nicht implementiert.

Wesentliche Entscheidungen: bestehende Python/PostgreSQL-Laufzeit, getrenntes Researchschema, ausschließlich Execution-owned Alpaca-GET, credentialfreie Query-/Researchrollen; zunächst SPY/QQQ raw IEX, keine Erweiterung von Tradingbarfenstern. FIRST_OBSERVED plus Ingestionfilter verhindert unbelegte historische Verfügbarkeit. Commitserialisierte Snapshotversion verhindert spätes Einschleusen zwischen Queryseiten. Begrenzte dauerhafte Jobs, gemeinsames Account-/Hostbudget und Safetypriorität. News/Fundamentals/Makroports bleiben ehrlich ohne echten Adapter. Kein LangGraph, TimescaleDB oder Backtesting in diesem Change.

## Aktuell ausgeführte Prüfungen

| Prüfung | Tatsächliches Ergebnis |
|---|---|
| `./scripts/openspec.ps1 --version` |1.14.1, gepinnter Wrapper |
| `./scripts/openspec.ps1 validate --all --strict` |12bestanden,0Fehler:8Main-Specs/4Changes |
| Anforderungs-/Szenarien-/Taskabgleich |16/28/9, vollständige Zuordnung, acyclische Dependencies |
| `./.venv/Scripts/python.exe scripts/validate-contracts.py` |OAS3.1.2,46Operationen,25Schemas,24Schema-/28Response-/4Problembeispiele und bestehende Negativen bestanden |
| `./.venv/Scripts/python.exe scripts/scan-source-secrets.py` |732Quelldateien,0Befunde |
| `git diff --check` |bestanden |
| Gezielte bestehende Regression |85passed in86.87s, siehe Auswahl unten |

Regression mit `.venv/Scripts/python.exe -m pytest`:
`tests/unit/test_domain.py tests/unit/test_market_state.py tests/unit/test_decision.py tests/security/test_market_gateway.py tests/security/test_execution_isolation.py tests/integration/test_replay.py tests/integration/test_jev_shadow.py -q --basetemp=.tooling/output/research-pytest-01 --tb=short`.

Erster Sandboxlauf:81passed,3failed,1error wegen temporärem Fixture-/lokalem PG-Zugriff. Wiederholung mit eigenem Testordner und freigegebenem Zugriff auf isolierte lokale PostgreSQL-Testdatenbanken:85passed. Kein Anwendungscode oder Test zur Fehlerumgehung geändert. Kein erneuter vollständiger Browser-/Gesamt-CI-Lauf, kein semantischer Breaking-Diff oder Clientneugenerierung nötig/belegt: kanonische Laufzeitverträge und Anwendungscode wurden in dieser Planung nicht geändert.

## Unabhängiger Planungs-/Safetyreview

Read-only Review prüfte Vision, AGENTS/Config, Proposal, Deltas, Design, Tasks und Testzuordnung. Vier Befunde im Plan behoben: Snapshotserialisierung statt reinem Timestamp, exaktes `/api/v1/research` und CSRF, gemeinsames Anbieterbudget mit Safetyreserve sowie unbekannte Kalenderhorizonte. Nachreview fand keine weiteren harten Planungsblocker. Ergänzende Hinweise übernommen: kurzer SQLversionslock erst nach Provider-I/O, Tests für Account- und Hostlimit/Tradingkonkurrenz und unbekannte Nutzungsrechte als Sperre vor Provider-I/O. Der Review zertifiziert keine noch fehlende Implementierung.

Safetyinvarianten: Agenten besitzen keinen Submitpfad; Research erhält keine Trading-Schreibrechte; deterministische Riskgates, LIVE-Sperre, Default DISABLED/Restartpause, Decimal/UTC, Kill/Pause/Reconciliation und immutable Handelsreferenzen bleiben unverändert. Gezielte bestehende Isolation-/Replaytests bestanden. Neue Researchrollengrenzen und Researchlasttests bleiben bis Apply **unverifiziert** und sind Pflichtabnahmen, keine heutigen Erfolge.

## GitHub und Grenzen

Neun offene Issues42–50 für MDR-01bis09 in `fjure/agentic-trading-platform`, mit Managedblocks, Labels, Requirements, Pfaden, Akzeptanz, Tests und verlinkten Abhängigkeiten. Getrenntes [aktuelles Mapping](../../../docs/development/github-research-issue-sync.json). Der offizielle GitHub-MCP ist nicht im Sitzungskatalog; autorisierter GH-CLI-Fallback verwendet. Kein PR erstellt, keine manuell gepflegten Inhalte/Labels gelöscht. Einzel- und repositoryweiter Read-back mit Inhaltsprüfung; finale Wiederholung:0erstellt/0aktualisiert/9unverändert,9Read-backs verifiziert. Zusätzlich alle neun Inhaltshashes gegen die aktuellen Source-Tasks und sämtliche lokalen Planungs-/Visionlinks geprüft.

Context7-Aufruf wegen Monatsquota ohne Dokumentationsinhalt; offizielle OpenSpec-/Providerdokumentation als Fallback im Design verlinkt. Keine Installation, Authentifizierungs- oder globale MCPänderung. Persönliche Providerentitlements/Nutzungsrechte nicht geprüft; kein Secret gelesen und kein echter Providerabruf/Order ausgeführt. Vorhandene fremde Änderungen an README und Angular-Konfiguration erhalten.

## Nächster erlaubter Schritt

Gesonderter Nutzerauftrag für `$openspec-apply-change market-data-and-research-foundation` nach Vorlage dieser Artefakte. AGENTS verlangt erfüllte Ready-Gates und getrennten Apply-Auftrag; der lokale Propose-Skill sagt: "After the planning artifacts are complete, stop." Das ist die bestehende Prozessgrenze, keine neue technische Zugangsfrage. Anschließend MDR-01 beginnen und den ersten vertikalen Pfad MDR-02/03 aufbauen; keine weitere Featureplanung nötig. Ein später empfohlener eigener Change ist `point-in-time-research-backtesting`, sobald diese Grundlage tatsächlich implementiert und zertifiziert ist.
