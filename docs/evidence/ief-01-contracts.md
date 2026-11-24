# IEF-01 – Vertragsnachweise

Umfang: ausschließlich die Vertragsgrundlage vor der Implementierung, auf Basis von `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Diese Nachweise zertifizieren weder HTTP-Implementierungen noch menschliche Bedienbarkeit, externe Verarbeitung oder Handel. IEF-01 bleibt unvollständig, solange seine menschliche Abnahme aussteht.

## Gelieferte Vertragsflächen

- 16 additive Operationen auf 15 neuen Pfaden: Präferenzen GET/PUT; Portfolio; Portfolioanalysen POST/Detail; Insights; Strategieliste/Versionsdetail; Aufgabenliste/Detail; Aktivität; Paper-Vorschau/geführte Anforderung; zweckgebundene Portfolioverarbeitung mit Vorschau/Status/Entscheidung im vorhandenen Transportnamensraum der Researchagenten.
- 45 neue Komponentenschemas; 38 gültige öffentliche Beispielfixtures und 58 aussagekräftige strukturelle Ablehnungsprüfungen. Das eigenständige Schemabündel enthält alle transitiv benötigten Definitionen (67), spiegelt die kanonischen Komponenten exakt und validiert alle drei Wurzelbeispiele unabhängig.
- `contracts/ief-operations.json` ordnet jede Operation ihrem Implementierungspaket, Requirement-IDs, geplanten Laufzeitszenarien und der Routenparitätsabnahme IEF-09 zu. Neue GETs und Vorschauen haben keine Providerwirkung. Quellen-/Aktionsreferenzen enthalten typisierte Identitäten und keine freien Ziel-URLs. Sitzung und Akteurszuordnung bleiben serverseitig maßgeblich.
- DEMO erfordert seine ausdrückliche Run-Identität; die Namensräume FAKE und ALPACA_PAPER bleiben getrennt. Nullable Kennzahlen tragen Einheit, Methodenrevision, Begründungscodes sowie Quellen- und Zeitnachweise. Unbekannte Daten werden niemals durch eine erfundene Null ersetzt.
- Vorschauterms verwenden ein neues geschlossenes Schema mit den exakten bestehenden LIMIT/DAY/REGULAR-Feldern ohne deren abschließende Bestätigung. Der geführte Commit enthält seine eigene ausdrückliche Bestätigung, Vorschauversion/-hash und Kontogeneration. Bestehendes Orderanforderungsschema und bestehende Route bleiben unverändert. Strategieprovenienz ist im Strategiezweig erforderlich und im Portfoliozweig verboten; richtungsabhängige Bruttogrenzen und blockierte Vorprüfungen werden im Schema geprüft.
- Der Portfolioerklärungszweck hat eigene Varianten für Vorschau, Entscheidung, Status und Widerruf. Zweckunions enthalten die unveränderten Hypothesenvarianten. Die Projektion akzeptiert ausschließlich begrenzte minimierte deterministische Befunde; rohe Konten, Positionstabellen, Präferenzen, Prompts und Zugangsdaten fehlen. Kostenobergrenzen sowie reservierte/abgerechnete Beträge verwenden vorzeichenlose Decimalstrings. Erforderliche Tool-/Datenallowlists dürfen nicht leer sein.

## Exakte Verifikation

Die Befehle liefen aus `.worktrees/ief-01`; gemeinsam genutzte gepinnte Abhängigkeiten wurden in das ignorierte lokale `.tooling/contracts` kopiert. Globale Konfigurationen wurden nicht verändert.

| Prüfung | Ergebnis |
| --- | --- |
| `.venv/Scripts/python.exe scripts/validate-contracts.py` (gemeinsame Root-venv) | PASS: 133 Operationen; alle bestehenden Fixtures; 38 gültige IEF-Fälle, 58 Ablehnungsfälle, Schemaspiegel und eigenständige Wurzelbeispiele |
| Gepinnter Node 22.23.3 / Redocly 2.62.1 `lint contracts/http/control-api.openapi.yaml --config .tooling/contracts/redocly.yaml` | PASS, keine Warnungen |
| `python scripts/check-contract-breaking.py --base-ref 82139cd` / oasdiff 1.33.0 | PASS, keine Breaking Changes, keine Warnungen |
| `./scripts/check-angular-client.ps1 -WriteClient` / OpenAPI Generator 7.26.0 mit SHA-geprüfter JAR / Temurin 17.0.20.1+1 | PASS: zwei unabhängige Generierungen über 541 Dateien bytegleich; generierter Client geschrieben |
| Dasselbe Generierungsskript / Angular 22.2.2 / TypeScript 6.0.2 | PASS: generierte Services/Modelle, alle bestehenden Fixtures, alle 38 IEF-Fixtures sowie negative Typfixtures für falschen Zweck und fehlende menschliche Bestätigung kompilieren |
| `.venv/Scripts/python.exe -m pytest tests/contracts/test_ief_contract_baseline.py tests/contracts/test_contract_stages.py -q --disable-warnings` | PASS: 14 Tests |
| Struktur- und Bytevergleich mit dem exakten Git-Blob von `82139cd` | PASS: alle 453 bestehenden Komponentenschemas und alle 117 bestehenden Operationen unverändert; Entfernen des zusätzlichen Tags, des neuen Pfadabschnitts und des Schemasuffixes stellt die exakten UTF-8-Bytes der Basis wieder her |

Die Basisdekodierung benötigte eine ausdrückliche UTF-8-Korrektur in `scripts/check-contract-breaking.py`: Die Windows-Locale verfälschte ansonsten deutsche Basistexte und erzeugte eine falsche Readiness-Warnung. Der Regressionstest vergleicht die Helperausgabe mit den tatsächlichen Git-Blob-Bytes. Nach der Korrektur ist die Breaking-Prüfung sauber. Die Standardausrichtung von Git-Diffs kann wiederholte YAML-Zeilen irreführend früheren Schemablöcken zuordnen; `git diff --minimal` oder `--diff-algorithm=patience` zeigt die ausschließlich additive Änderung.

## Gestufte Routenabnahme und Wiederanlauf

`contract_stages.py` nimmt ausschließlich die ausdrückliche IEF-Matrix mit 16 Operationen von der Routenparität aus, solange die verlinkte Aufgabe IEF-09 offen ist. Der Abschluss von IEF-09 entfernt alle IEF-Ausnahmen. Fehlende/doppelte Abnahmezeilen sperren sicher; unbekannte Operationen werden niemals ausgenommen. Andere vorhandene Routenparitätsprüfungen behalten ihr exaktes Verhalten. Dies ist die autorisierte Vertragsvorstufe, kein Nachweis installierter Endpunkte. Lokale Analyse, Modellverarbeitung und Paperfähigkeiten bleiben deaktiviert beziehungsweise erklärt unverfügbar, bis ihre späteren Pakete echte Implementierungen und Tests liefern. Es wurden keine Serverstubs ergänzt.

## Begrenzter Trading-Sicherheitsreview

Angewendet wurden `api-contract-workflow` und `trading-safety-review`. Die Vertragsbefunde unten bestehen an der Schemagrenze; sämtliche Laufzeitprüfungen bleiben bis IEF-04/07/09 UNVERIFIED. Es wurden keine Broker-/Providerzugangsdaten oder echten Endpunkte verwendet.

| Invariante | Vertragsnachweis / Test | Laufzeitbewertung |
| --- | --- | --- |
| Agenten vom Brokersubmit isoliert | Keine neue Brokersubmitroute oder ausführbare Aktions-URL; begrenzte Ressourcenreferenzen, Ablehnungsfälle für unbekannte Felder/URLs | UNVERIFIED; bestehende Execution-Isolation bei Integration erneut prüfen |
| Deterministische RiskDecision, aktuelle Execution-Gates | Unverbindlicher Vorschaustatus, unveränderliche Terms-/Hash-/Konto-/Markt-/Ledger-/Policybindungen; bestehende Admission unverändert | UNVERIFIED; finale Wiederholungsprüfung, Reservation und Submitfence in IEF-07 erforderlich |
| LIVE unerreichbar, sicher gesperrter Neustart | Vorschaukontext fest ALPACA_PAPER; keine Mutation für Modus/Start/Restore ergänzt | UNVERIFIED; vorhandene Neustart-/Modussicherheitssuite erforderlich |
| Decimal/UTC und atomare Budgets | Kanonische Decimal-/UTC-Referenzen; ungültige Zahl-/Exponent-/Zeitzonenfälle; Ablehnung negativer Kosten/Budgets | Strukturell PASS; Konkurrenz atomarer Reservationen UNVERIFIED |
| Unabhängige Pause/Kill und Reconciliation | Bestehende Kontroll- und Reconciliationverträge bytegleich und semantisch unverändert | UNVERIFIED; keine Laufzeitänderung oder -behauptung |
| Zugangsdaten ausschließlich für Execution, Redaktion | Alle neuen Objekte geschlossen; keine Zugangsdaten-/Prompt-/Kontonutzlast in Erklärungen; Ablehnungsfälle für falschen Zweck und rohe Positionen | Strukturell PASS; Laufzeit-Prozessisolation/Telemetrie UNVERIFIED |
| Unveränderliche Provenienz und Audit | Ursprüngliche Terms/Proofs, Strategieeignung/-evidenz, akteursgebundene Semantik, Trace-/Quellrevisionen | Strukturell PASS; Akteursisolation/Replay/Ablaufrennen UNVERIFIED |
| UNKNOWN-Erhalt, Timeoutklärung, Teilfills/Cancelrennen | Bestehende PaperOperation-/PaperOrder-/Fill-/Cancel-Schemas unverändert; keine Retry-/Submitoperation eingeführt | UNVERIFIED; erforderliche Laufzeitszenarien IEF-G03/G08 bleiben offen |
| Modellausfall kann Sicherheit nicht deaktivieren | Neuer Erklärungszweck in derselben Transportfamilie isoliert; lokale Analyse unabhängig lesbar | UNVERIFIED bis Supervisor-/Gatewayimplementierung |

Fremde opake IDs, Sitzungsverlust, einmaliger Vorschauverbrauch, konkurrierende Fills, Durchsetzung aktueller Gates, Providerzustellung und Kosten erfordern echte Transport-/Speichertests. JSON Schema kann diese Laufzeitfakten nicht belegen. Das Paket darf nicht allein aufgrund dieser Vertragsnachweise als vollständig zertifiziert werden.


**Aktueller Abnahmezeitpunkt:** Nach dem späteren Nutzerauftrag werden alle drei menschlichen Prüfungen in IEF-09 durchgeführt. Frühere Aussagen in diesem Nachweis über eine menschliche Freigabe als technisches IEF-01- oder Übergabegate sind historisch. Der Prüfstatus bleibt HUMAN_UX_VALIDATION_PENDING.
