# IEF-01 · Technischer Nachweis

Der lokale Worktree `.worktrees/ief-01` auf `codex/ptv-107-ief-foundation` enthält die additive Vertragsbasis und den klickbaren Prototyp für S1–S10 und J1–J6. Ausgangs-HEAD und zuletzt verifizierter Remote-main sind `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Das [Quellenmanifest](ief-01-manifest.json) identifiziert die lokal geprüften Änderungen samt Build, da der HEAD die Arbeitsbaumänderungen nicht enthält.

Der Prototyp verwendet feste synthetische Daten. Die Fachaktionen der neuen Oberfläche sind Simulationen. Bestehende Funktionen sind über „Daten und Betrieb“ zugänglich. Die 16 additiven Operationen sind in der [Operationsmatrix](../../contracts/ief-operations.json) den späteren Fachpaketen zugeordnet. Der Prototyp startet weder einen Provider- oder Researchlauf noch eine Paperorder.

| Prüfebene | Nachweis |
| --- | --- |
| OpenSpec | 22 Einträge strikt validiert, 0/9 Paketabschlüsse |
| Vertragsbasis | 45 neue Schemata und 16 Operationen auf 15 Pfaden, 38 Positivbeispiele und 58 Negativfälle; Redocly und oasdiff gegen Ausgangs-HEAD grün; 541 Dateien zweimal bytegleich generiert und TypeScript geprüft |
| Vollständige Contract-Suite | `pytest tests/contracts -q` am finalen Stand: **2.483 bestanden**. Frühe Fehler in APF-, Paper- und Quant-Paritätstests durch explizite Zuordnung der IEF-Erweiterungen behoben; alte Pfad- und Schema-Digests bleiben unverändert |
| Produktionsbuild | Angular und Router 22.2.2, Initialbundle 487,59 kB. Bestehende Budgetwarnung für die Supervisor-CSS-Datei |
| Browserregression | **44 bestanden** mit Chromium und synthetischer Loopback-Gegenstelle, einschließlich Prototypwege, Legacy-Links, Supervisor, Demo, Paper und Companion. Geprüft sind ausgewählte Kontraste und repräsentative Ansichten bei 200 Prozent Textgröße. Manuelle Screenreader- und Browserzoomprüfung bleiben offen |
| Lokale Python-Regression | 1.717 Unit-Tests bestanden, drei plattformspezifische Skips. Weitere 114 Evaluation-, Property- und Safety-Tests bestanden |
| Quellscan | 2.083 Quelldateien, 0 Treffer; `git diff --check` sauber |
| PostgreSQL-Rollenprüfungen | **4 bestanden** mit frischem PostgreSQL 18.6 im isolierten Docker-Projekt. Geprüft sind die Backtest-Rolle sowie die Execution-Szenarien `denials`, `guards` und `startup` |
| GitHub | Neun Statusupdates über den autorisierten GH-CLI-Fallback nach MCP-403. Zweiter Lauf 0 Updates, neun offene Issues, alle 26 Kanten in beiden Richtungen bestätigt. Remote-Checks für den Ausgangs-HEAD sind grün |

Detailnachweise stehen im [Vertragsbericht](ief-01-contracts.md), im [Prototypbericht](ief-01-prototype.md), in der [Baseline](ief-01-baseline.md), im [unabhängigen Review](ief-01-review.md) und im [Safetyreview](ief-01-safety.md). Aufnahmen des lokalen Produktionsbuilds: [Desktop-Überblick](ief-01-desktop.png), [mobile UNKNOWN-Ansicht](ief-01-mobile-unknown.png).

`scripts/test-research-db.py --roles-only --junitxml .tooling/ief-01-roles.xml` bestand nach Wiederherstellung des Docker-Endpunkts mit Exitcode 0 und **4 Tests in 22,92 Sekunden**. Erste Läufe mit vorhandenem und frisch gebautem Testimage deckten fehlende Gruppen im synthetischen Setup auf. Die Test-Hilfen legen die Gruppen für die vollständige Migrationskette vorab an. Der temporäre Migrationslogin bleibt auf `NOCREATEROLE` beschränkt. Der erfolgreiche Lauf verwendete den regulären Image-Build und ein frisches internes Netz. Container, Netz und Volume wurden anschließend entfernt. Sechs ergänzende lokale Prüfungen zu Testziel und statischen Execution-Grenzen bestanden. [Startanleitung](../runbooks/ief-prototype.md#technischer-nachlauf-der-postgresql-rollentests)

**HUMAN_UX_VALIDATION_PENDING.** Der Nutzer hat bestätigt, dass aktuell keine Testpersonen verfügbar sind. IEF-T-C06 verlangt fünf fachfremde Personen und die formative Freigabe dieser Fassung. Die [Anleitung](../runbooks/ief-prototype.md) und das [Testprotokoll](ief-human-ux-protocol.md) liegen bereit. Die nachgelagerten Pakete hängen an dieser Freigabe. Remote-CI der lokalen Änderungen und integrierte IEF-09-Abnahme sind ausstehend. Der [Apply-Stand](ief-apply-status.md) nennt die Abhängigkeiten pro Paket.

**Aktueller Abnahmezeitpunkt:** Nach dem späteren Nutzerauftrag werden alle drei menschlichen Prüfungen in IEF-09 durchgeführt. Frühere Aussagen in diesem Nachweis über eine menschliche Freigabe als technisches IEF-01- oder Übergabegate sind historisch. Der Prüfstatus bleibt HUMAN_UX_VALIDATION_PENDING.
