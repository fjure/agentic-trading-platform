# ARP-10 · Integrierte Readiness-Abnahme

Die technische Offline-Abnahme und die modellgebundene Betriebsfreigabe werden getrennt bewertet. Für den Betrieb ist eine Freigabe pro Provider-, Modell-, Credential-, Tarif- und Zweckbindung erforderlich.

| Bereich | Implementierung | Reproduzierbare Offline-Prüfung | Betreiberfreigabe und Realnachweis |
| --- | --- | --- | --- |
| Compose, Setup und Egress | Standardmäßig gesperrte Providerpfade, Setup als separater Prozess, eigene Schlüssel- und Journal-Mounts, feste Providerziele | `tests/security/test_research_production_compose_gate.py` für beide Provider in der kanonisch gerenderten Komposition | Konto, API-Schlüssel, Modell, Providerbedingungen und Datenrechte separat bestätigen |
| Modellbezogener Smoke und Kosten | Reservierung vor Versand, feste synthetische Nutzlast, einmalige Admission, gebundene Rechnung und konservative Behandlung unbekannter Usage | `tests/integration/test_arp_provider_smoke.py`, `tests/integration/test_arp_model_attempts.py` und `tests/unit/test_smoke_budget.py` | Budgetgrenzen und Tarifnachweise bestätigen. Echten Verbindungstest für die jeweilige Bindung separat autorisieren |
| Dialog, Discovery und Quant | Exakte Sendefassung, getrennte Quant-Freigabe, versionierter Report, Discovery mit eigener Autorisierung | Browser-Gate und SQL-Assertions über `scripts/test-research-browser.py`, separate Discovery-Regression im Datenbankgate | Verarbeitungszwecke und Zeitplan je Bindung freigeben |
| Kill, Pause und Wiederanlauf | Deterministische Risk-/Execution-Grenzen, dauerhafte Fences, Widerruf, Journal und Kostenabgleich | `scripts/test-research-db.py` mit Crash-, Wiederholungs-, Last- und Rollback-Fällen | Abschalt- und Wiederanlaufübung auf der tatsächlichen Betreiberinstallation durchführen |
| NLP-Freigabe | Goldstandard, Scorer, paarweiser Vergleich, signierte Review-/Custody- und Registrierungsnachweise | `python -m pytest tests/evaluation` mit synthetischen Testschlüsseln und Antworten | Offen. Verifizierte Beobachtungen und Prüfevidenz aus getrennten KI-Reviewer-Profilen, unabhängige Schlüssel-Pins, externe Registrierung, Custodian-Öffnung und drei autorisierte Holdoutläufe je Bindung erforderlich |

Der spätere Betriebsablauf steht im [Research-Betreiber-Runbook](../../openspec/changes/agent-research-production-readiness/operator-runbook.md) und im [Rollback-Runbook](../runbooks/research-provider-rollback.md).

Der Browserfall verwendet eine vorbereitete synthetische Smoke-Evidenz für die initiale Dialogaktivierung. Der echte Einmal-Smoke und dessen dauerhafte Autorisierung laufen separat im Datenbankgate. Der Rollback-Fall prüft den signierten Supervisor-Rückkanal. Der separate Setup- und Widerrufspfad wird durch `tests/integration/test_arp_model_attempts.py` mit tatsächlicher Setup-App, Einmal-Grant und Origin-Prüfung abgedeckt.

Der unabhängige Safety-Review prüfte die geänderten Netzwerk- und Mountgrenzen, die gebundene Regressionsentscheidung und den Rollback. Fünf gezielte Negativtests bestanden. Die abschließende Zertifizierung hängt von den vollständigen Laufzeitergebnissen ab.

Die erneute lokale Abnahme am 23.11.2026 bestätigte 2.481 Vertragstests, sämtliche Schema- und Beispielprüfungen und einen Quellscan ohne Credentialbefund. Die schnelle Research-Auswahl bestand mit 178 Fällen. Zwei POSIX-Dateirechtefälle benötigen das Linux-Gate. Die vollständige Datenbanksuite prüft diese Laufzeitgrenzen zusätzlich in Linux-Containern. Die beiden direkten Release-Downloads für JDK und oasdiff ergaben die festgelegten SHA-256-Werte und vermeiden das anonyme GitHub-API-Limit beim CI-Bootstrap.

Der vollständige Datenbanklauf bestand mit 14 Fällen in 408,96 Sekunden. Er umfasste PostgreSQL-Backup und Restore, tatsächliche Produktionskomposition für OpenAI und Anthropic, Recovery, synthetischen Smoke, Setup-Widerruf sowie Rollback. Der Lastfall bestätigte für jede Safetyaktion die Grenze unter fünf Sekunden, null Brokerorders und unveränderte geschützte Finanzdaten. Der neue Evaluierungsadapter erhält eine zusätzliche eigenständige PostgreSQL-Prüfung und wird im endgültigen Gate erneut mit ausgeführt.

## Gesamtstand des Changes

ARP-01 etablierte den gesperrten Providerkatalog. Es folgten ARP-02 für Gateway und Egress, ARP-03 für Setup und Credentials, ARP-04 für Autorität und Budget, ARP-05 für Pflicht-CI, ARP-06 für Referenzen und Evaluation, ARP-08 für den modellbezogenen Smoke, ARP-07 für den Betreiberablauf, ARP-09 für Isolation und Recovery sowie ARP-10 für die integrierte Abnahme. Die Git-Historie und die Issue-Zuordnung in `github-issues.json` liefern die einzelnen Implementierungsstände.

| Bereich | Nachgewiesenes Verhalten |
| --- | --- |
| Gateway | Signierter privater Supervisor-RPC, einmaliges Journal, feste Host-, Pfad- und Modellbindung, TLS über den eingeschränkten Egress-Proxy. OpenAI und Anthropic werden gegen synthetische TLS-Gegenstellen geprüft. |
| Credential-Lifecycle | Separater Setup-Prozess, kurze Einmal-Grants, Origin- und Sitzungsprüfung, eigene Mounts und Runtimeidentitäten, generationengebundene Autorität, Rotation und dauerhafter Widerruf. |
| Provideraktivierung | Gespeicherte Credential, technischer Verbindungsnachweis, freigegebener Zweck und Datenrechte sind eigene Voraussetzungen. Fehlende oder veraltete Evidenz sperrt die Bindung. |
| Smoke und Kosten | Modellbezogene Token- und Kostenobergrenze, Tarifbindung, genau ein synthetischer Request nach Betreiberzustimmung. Unbekannte externe Kosten halten die konservative Reserve, eine spätere Rechnung bindet den ursprünglichen Attempt. |
| Verarbeitung | Die exakte Fassung wird vor Versand bestätigt. Eine geänderte Vorschau, ein alter Fence oder widerrufene Autorität sperren den Versand und die Ergebnisübernahme. |
| Proaktive Forschung | Eigene Rechte, Zeitplan und persistente Datenbindung. Eine Discovery-Idee startet kein Experiment. Quant-Zulassung und Experimentfreigabe bleiben separate Aktionen. |
| Angular | Materialoberfläche für lokale Klärung, sichere Einrichtung, Providerstatus, Vorschau und Smoke, serverseitigen Zeitplan, gebundene Quant-Freigabe und versionierte Reports mit Reload. |
| NLP | 48 synthetische Referenzfälle in vier Gruppen, 24 Holdoutfälle in der Splitstruktur, getrennte Kennzahlen und kritische Ausschlusskriterien, drei Wiederholungen und gepaarte Regression. |
| Review und Custody | Zwei getrennt gebundene KI-Reviewberichte, deterministische Validierung, signierte Registrierung und Custodian-Freigabe, unabhängige Schlüssel-Pins und einmalige Öffnung. Offline-Prüfungen verwenden synthetische Antworten und Schlüssel. |
| CI | Schnelle Suite, tatsächliche PostgreSQL-Prüfungen, Angular-Build, Browserkette und SHA-gebundener Evidenzaggregator. Fehlende Artefakte, Skips oder abweichende Pflichtfall-IDs sperren die Gesamtfreigabe. |
| Safety | Aktive Quant- und Researchlast, Workercrash und Gatewayfehler. Pause, Kill und Reconciliation bleiben jeweils unter fünf Sekunden, Brokerorders bleiben aus und geschützte Finanzdaten bleiben unverändert. |
| Rollback | Zeitplan pausieren, Autorität widerrufen, optionale Gateway-/Egress-Dienste stoppen, dieselben Daten und Journale erhalten, den geprüften Offlinepfad ohne destruktive Rückmigration starten. |

## Offene Betreiberabnahme

Die tatsächliche Unabhängigkeit der späteren KI-Reviewmodelle, ihre Provider- und Prompt-Provenienz, eine separat betriebene Custodian-Identität, der externe Registrierungsanker und die Betreiberentscheidung benötigen konkrete betriebliche Belege. Die lokale technische Reviewprüfung erfolgte mit synthetischen Testschlüsseln und Antworten. Eine fachliche Modellfreigabe erfordert die unabhängigen Annotationen und vollständigen Holdoutberichte für die registrierte Modellbindung.

Der Betreiber prüft zunächst Konto, Providerbedingungen, Datenrechte und Schlüsselbeschränkungen. Danach bestätigt er das konkrete Modellprofil und Budget und autorisiert genau einen begrenzten echten Verbindungstest. Anschließend folgen die separat autorisierten Holdoutläufe und die fachliche Regressionsentscheidung. Erst mit diesen Nachweisen werden Dialogzweck und gegebenenfalls Discovery-Zeitplan ausdrücklich freigegeben. Der Ablauf steht im [Betreiber-Runbook](../../openspec/changes/agent-research-production-readiness/operator-runbook.md) und im [Evaluierungsrunbook](../runbooks/research-model-evaluation.md).

Die synthetische Abnahme belegt die Implementierung und Sicherheitsmechanik. Modellblindstellen und fachliche Restunsicherheit müssen in der späteren Betreiberentscheidung anhand tatsächlicher Reviewevidenz bewertet werden.

Die neu ausgelieferte Evaluierungskomposition wurde zusätzlich durch einen echten PostgreSQL-Fall mit produktiver ProviderAuthority, Kostenpolicy, signiertem Gateway und CONNECT/TLS geprüft. Er bestätigt den vollständigen Expertenkontext, keine Companion-Fallablage, reine Metadaten im gemeinsamen Ledger, private Rohantworten, konservative Reserven, Sperre wiederholter Attempts und die globale exklusive Lease. Factory-Negativfälle verweigern falsches Encoding, Probeprofil und abgelaufene Freigaben vor dem Datenbankzugriff. Der Privacybefund aus dem unabhängigen Review wurde damit behoben.

Das Produktionsimage enthält den privaten Operator-Einstieg und das optionale Overlay `compose.research-evaluation.yaml`. Ein eigener Pflichtfall startet den Operator ohne Netz und bestätigt, dass weder Testfunktionen noch Holdouttexte im Image enthalten sind. Das Verhalten der Komposition ist im [Evaluierungsrunbook](../runbooks/research-model-evaluation.md) reproduzierbar beschrieben.

## Abschließende lokale Prüfevidenz

| Kommando oder Gate | Ergebnis |
| --- | --- |
| `python scripts/test-research-db.py --junitxml <private-temp>/arp10-final-db.xml` | 16 bestanden in 397,58 s, einschließlich produktivem Evaluierungsadapter, beiden TLS-Providern, Verpackung, Rollback und Last |
| Fast-Auswahl aus `.github/workflows/research-integration.yml` im netzisolierten Linux-Testimage | 180 bestanden, keine Skips, Pflichtfall-Recorder positiv |
| Kanonischer Browser-Runner `scripts/test-research-browser.py:execute` mit gepinntem Produktionsbuild und synthetischer Laufzeit | Alle 7 Pflichtfälle bestanden, SQL-Nachweis und redigierter Evidenz-Recorder positiv |
| `python -m pytest tests/contracts -q` | 2.481 bestanden |
| `python scripts/validate-contracts.py` und `python scripts/check-contract-breaking.py --base-ref 0c26def4859265f6017665c8fa7432d5d521fee6` | Bestanden, null semantische Fehler. oasdiff meldet die bekannte allOf-Warnung am identischen Readiness-Endpunkt |
| Angular-Produktionsbuild | Bestanden, 869,47 kB Initialbundle, bestehende Größenwarnungen unter dem Fehlerlimit |
| Strikte OpenSpec-Validierung, Ruff und Credential-Quellscan | Bestanden, null Credentialbefunde |
| Abgeleitete Produktivtransport-Prüfung mit fehlender Usage | PostgreSQL samt Restore bestanden in 17,25 s, unklarer Ausgang, vollständige Reserve, keine Ergebnisübernahme und kein Replay |

Die lokalen Laufzeitartefakte enthalten synthetische Daten und verbleiben außerhalb der versionierten Evidenz. Der final gepushte Stand wird durch die SHA-gebundenen GitHub-Gates erneut geprüft.

Der Scorer-Einstieg nimmt zusätzlich den registrierten Manifest, die gebundene Entwicklungsreferenz und `--mode model` entgegen. Der Modellmodus verlangt den Holdout-Split und die strengen modellbezogenen Pflichtbeobachtungen. Der CLI-Regressionsfall verwendet ausschließlich öffentlich abgeleitete synthetische Testfälle und bestätigt die Sperre bei fehlenden Antworten. Diese Ergänzung und die gesamte Evaluierungs- und Evidenzsuite bestanden mit 40 Tests.

Der erste Research-CI-Lauf auf `1e1d011299fa93f42253006cddd079f0bf6ccabe` meldete bei der Chromium-Systemvorbereitung `missing_environment`. Das Ubuntu-Paketmirror lieferte die angeforderten Font-/Mesa-Pakete innerhalb des bisherigen 240-Sekunden-Limits nicht vollständig. Ausschließlich der Browser-Installationsschritt erhält nun ein begrenztes 600-Sekunden-Fenster. Die Testfristen, Pflichtfälle und fail-closed Aggregation bleiben verbindlich.

## Readiness-Matrix

`CIVerified` bezieht sich auf den abschließend geprüften Releasekandidaten. Die betriebliche Freigabe bleibt je realer Provider- und Modellbindung erforderlich.

| Bereich | Implemented | OfflineVerified | CIVerified | SecurityVerified | RequiresOperatorApproval | RealProviderVerified |
| --- | --- | --- | --- | --- | --- | --- |
| Gateway, Egress und beide Providerprotokolle | Ja | Ja | Ja | Ja | Ja | Nein |
| Setup, Rotation und Widerruf | Ja | Ja | Ja | Ja | Ja | Nein |
| Modellbezogener Smoke und Budgetledger | Ja | Ja | Ja | Ja | Ja | Nein |
| Dialog, Discovery und Quant bis Report | Ja | Ja | Ja | Ja | Ja | Nein |
| Private Evaluierung und Fachfreigabemechanik | Ja | Ja | Ja | Ja | Ja | Nein |
| Safety, Wiederanlauf und Rollback | Ja | Ja | Ja | Ja | Ja | Nein |

Der korrigierte Code-Commit `6ead1a62c6c04d29004cc14f73a9bbae1735b372` bestand [Research integration](https://github.com/fjure/agentic-trading-platform/actions/runs/45172565322) vollständig. CI bestätigte 181 Fast-Fälle, 16 Datenbankfälle, den integrierten Browserfall, fünf ergänzende Browserfälle und den SQL-Pflichtnachweis. Der Aggregator akzeptierte alle Artefakte für denselben ausgecheckten SHA.

Auch [Contracts](https://github.com/fjure/agentic-trading-platform/actions/runs/45172565411) bestand auf `6ead1a62c6c04d29004cc14f73a9bbae1735b372` vollständig, einschließlich reproduzierbarer Clientgenerierung, Vertragstests, Angular-Produktionsbuild und Supervisor-Browserregression. Damit ist die technische Offline-Abnahme grün. OpenSpec führt zehn abgeschlossene Implementierungstasks. Die Matrix bewertet die tatsächlichen realen Provider- und KI-Fachfreigaben weiterhin mit `RealProviderVerified = Nein`.

Die offizielle GitHub-MCP-Integration konnte lesen, verweigerte Issue-Änderungen jedoch mit HTTP 403. Der gemäß `AGENTS.md` autorisierte GH-CLI-Fallback synchronisierte den verwalteten Block. Ein erneuter offizieller Read-back bestätigte Titel, Labels, Evidenzlink, eindeutige Taskzuordnung und die abgeschlossenen Abhängigkeiten.
