# Architecture Review — IEF Propose

Datum: 2026-11-23. Geprüfte Implementierungsbasis: `0c26def4859265f6017665c8fa7432d5d521fee6`. Umfang: statischer Architektur- und Konsistenzreview der Planung, keine Implementierungsabnahme, kein Apply und keine Runtimezertifizierung.

## Unabhängigkeit und geprüfter Umfang

Der Reviewer hat zuvor `portfolio-read-model.md`, `agent-task-model.md` und `guided-order-design.md` beigetragen. Diese Eigenbeiträge werden hier ausdrücklich **nicht** als unabhängiger Alleinnachweis verwendet. Der unabhängige Review richtet sich auf die vom Hauptagenten erstellten `design.md`, Capability-Deltas, `tasks.md` und `work-packages/IEF-01.md` bis `IEF-09.md` sowie deren Verknüpfung mit UX-, Journey- und Testplan. Eigenbeiträge dienen ausschließlich als zusätzliche Vergleichsdokumente. Die Planung benötigt daneben den getrennten Safety-/UX-/Verifikationsreview.

Quellabgleich: `packages/domain/models.py`, `packages/persistence/ledger.py`, `packages/agent_research/{registry,runs,run_state,processing}.py`, `services/runtime/{agent_service,agent_status}.py`, `services/control_api/paper_dto.py`, `services/control_api/migrations/versions/0020_paper_read_models.py`, `services/worker/paper_human_limit.py`, `packages/risk/paper_limits.py`, `services/execution/paper_submit.py`, kanonischer OAS und APF-/ARP- sowie Paper-/Supervisor-Spezifikationen. Es wurden keine Tests ausgeführt oder als bestanden behauptet.

## Ergebnis nach Korrekturen

Keine offenen P1-/P2-Architekturbefunde im erneut gelesenen Planungsumfang. Die folgenden während des Reviews gemeldeten P2-Befunde wurden vom Hauptagenten korrigiert und erneut geprüft:

| Befund | Korrektur und Read-back |
| --- | --- |
| Paper-only-Modell stand neben angekündigtem DEMO-/FAKE-Portfolio ohne eindeutige Herkunft. | `design.md`, Vertragspräzisierung, sowie IEF-03 benennen echte Readports; DEMO verlangt demoRunId, FAKE den Snapshotreadport, Paper die isolierten Ports. Die interne echte Quellrevision muss konsistent projiziert werden; asOf ersetzt keine Ledgerrevision. Fehlende Provenienz bleibt unavailable. Bestehender OAS: `/api/v1/snapshot`, `/api/v1/demo/runs/{runId}/snapshot`, `SupervisorSnapshot`. |
| IEF-G01 verlangte allgemeine Kostenobergrenze und ließ fehlende optionale Marktwert-/Gewichtsprojektionen jede Bestätigung sperren. | `specs/guided-paper-experience/spec.md`, IEF-G01/T-G01, unterscheidet BUY-Obergrenze und SELL-Mindestbruttoerlös sowie verpflichtende Gategrundlagen und optionale unbekannte Gewichte. `design.md` D5 erhält Nachkaufsperre und bestehende Risk-/Executionprüfung. |
| IEF-A01 verlangte für lokale synchrone Analyse eine Laufidentität, obwohl D3 runId=null definiert. | `specs/investment-task-experience/spec.md`, T-A01, verwendet Quellidentität: analysisId für lokale Analyse, runId für Research. D3/IEF-04 bleiben synchron, begrenzt und ohne zweite Queue; IEF-06 projiziert beide Quellen. |
| Neuer Portfolioerklärungszweck ohne ausreichend konkrete Vertrags-/Persistenzerweiterung konnte als Wiederverwendung des Hypothesenkanals verstanden werden. | `design.md`, Vertragspräzisierung, IEF-01 und IEF-04 fordern zweckdiskriminierte Preview-/Decision-/Status-/Widerrufsschemas, Projektor-/Policy-/Schema-/Provider-/Budgetbindung und additive Purpose-/RunKind-/SQL-Constraintmigration im vorhandenen Processingstore/Supervisor. Der alte Hypothesenvertrag bleibt unverändert; keine Promptrekonstruktion und kein universeller Grant. |
| J3/J4 bezeichneten in Journey- und Testplan unterschiedliche Nutzerreisen. | `user-journeys.md`, `test-strategy.md`, IEF-05 und IEF-06 verwenden konsistent J3=Strategie, J4=freie Techfrage. Die J6-Fehlermatrix ergänzt den Wiederaufnahmeweg, statt einen anderen Sicherheitsumfang zu ersetzen. |

## Bewertete Architekturentscheidungen

- **Finanzwahrheit:** Experience ist eine begrenzte serverseitige Projektion vorhandener Ledger. Decimalstrings, UTC, Quelle, Revision, Bewertungszeit und getrennte Verfügbarkeit bleiben erhalten. Neue lokale Befunde sind abgeleitete Analysequellen, keine Kontobuchungen oder nachträgliche Performancehistorie.
- **Runtime:** Der lokale Analyse-POST benötigt keine Agentenruntime. Optionale Modellverarbeitung erweitert den vorhandenen Supervisor/Gateway eng zweckgebunden und nutzt bestehende Fences, Budgets, Dispatch-/Widerrufsgrenzen. IEF-06 führt weder Scheduler noch generisches Execute ein.
- **Paper:** Preview erzeugt keine Reservation/Brokerwirkung. Einmaliger Previewcommit führt in denselben menschlichen Anforderungs-, Risk- und Executionpfad; aktuelle finale Gates und UNKNOWN-Reconciliation bleiben verbindlich. Bestehende Orderoperationen werden kompatibel erhalten.
- **Verträge und Migration:** IEF-01 liefert OAS/Beispiele/Client zuerst und behauptet keine Routeparität durch Stubs. IEF-04 enthält Zweck-/RunKind-/Constraintmigration; additive Präferenzen/Metadaten erhalten vorhandene Historie. Rollback schaltet neue Fähigkeiten ab, ohne Finanz-/Auditdaten zu löschen oder alte Grants zu reaktivieren.
- **Abhängigkeiten:** IEF-04/06 und integrierte IEF-09-Abnahme hängen explizit an offenem ARP-10/#105. Vorhandene Quellklassen, Providerkonfiguration und geschlossene ARP-01–09 ersetzen weder integrierte Readiness noch gesonderte Betreiberfreigaben.

## Grenzen dieses Ergebnisses

Die konkreten OAS-Schemas, Migrationen und neuen RunKind-/Purposezulassungen entstehen erst im gesondert beauftragten Apply; dann sind insbesondere alte Hypothesenclients, Enum-/SQL-Constraints, Replay mit geänderter Nutzlast, neue Akteur-/Kontextcursor, konkurrierende Fills und Widerruf am Dispatchpunkt tatsächlich zu prüfen. IEF-09 und die paketbezogenen DoDs verlangen diese Nachweise bereits. Das rote externe Baseline-Contractsgate und offene ARP-Readiness bleiben eigenständige Abnahmeblocker; dieser Dokumentreview schließt sie nicht.

## Nachprüfung der veröffentlichten ARP-10-Basis

Der vorstehende Review und seine damaligen Blocker beziehen sich historisch auf `0c26def`. Nach dem vom Hauptagenten gemeldeten Remote-Read-back wurde am 23.11.2026 die neue veröffentlichte Basis `0442f29571e53b983e9e08f2180c4bd3536e0b6a` statisch nachgeprüft. Grundlage waren `git diff --stat 0c26def..0442f29` (39 veröffentlichte Dateien), gezielte Quell-Diffs, die neuen Evaluierungs-/Rollbacktests, `docs/evidence/arp-10-integrated-readiness.md` und die aktualisierten IEF-Planungsdokumente. Diese extern veröffentlichten Änderungen sind keine IEF-Implementierung. Der Reviewer hat weder eigene Git-Mutationen noch einen neuen Runtime-/CI-Lauf durchgeführt.

| Geänderter Baustein | Architekturabgleich zur IEF-Planung |
| --- | --- |
| `packages/agent_research/runs.py:RunStore.claim` | Optionaler gezielter run_id-Filter bleibt innerhalb derselben Queue, globalen Claimgrenze, Lease und Fence; keine zusätzliche Runtime. Der vorhandene ungefilterte Aufruf bleibt kompatibel. IEF muss diese globale Begrenzung auch für neue Erklärungszwecke erhalten. |
| `services/runtime/agent_interpretation.py` | `dispatch_authorized` extrahiert den vorhandenen autorisierten Versandpfad; `run` verwendet ihn weiter und übernimmt die bisherige Ergebnisannahme. Dies erleichtert private Evaluation, erteilt aber keinen neuen Portfoliozweck und ersetzt keinen neuen Projektor/Validator. |
| `services/runtime/agent_service.py` | Private in-process `app.state.research_evaluation`-Ports sind kein neues öffentliches HTTP-API und kein allgemeiner Experienceexecute. IEFs explizite Vertragsfamilien und autoritative Gates bleiben erforderlich. |
| `services/llm_gateway/provider_installation.py` | `parse_installation` validiert exakt gebundene Descriptorbytes; Größen-/Typprüfung bleibt fail-closed. Dies erweitert weder die freigegebenen Providerziele noch die Zwecke der IEF-Erklärung. |
| `services/runtime/research_evaluation.py` und Composeoverlay | Operatorgebundene Evaluation verwendet vorhandene Run-/Processing-/Budget-/Gatewaypfade, private flüchtige Projektionen und inhaltsfreie gemeinsame Metadaten. Die exklusive Komposition ersetzt den bestehenden Supervisorprozess; kein dauerhaft paralleler zweiter Supervisor wird als IEF-Fähigkeit eingeführt. Unbekannte Kosten behalten ihre konservative Bindung. |
| Neue Integrations-/Recovery-/Packagingtests | Quellassertions prüfen gezielte Claims ohne Übernahme fremder Runs, globale Exklusivität, keine Companion-Fallablage, Metadatenminimierung, unknown Usage mit voller Reserve und kein Replay; Rollback erhält bestehende Daten. Diese Assertions wurden gelesen, hier nicht erneut ausgeführt. |
| Contractbootstrap und CI | Downloadziele wurden bei identischen SHA-256-Bindungen von API-Assetrequests auf Releaseartefakte umgestellt; Pflichtfälle und fail-closed Aggregation bleiben. Der frühere Bereitstellungsfehler ist durch den nachfolgenden veröffentlichten Nachweis ersetzt. |

Aktueller Status gemäß erneutem Remote-Read-back des Hauptagenten und aktualisierter Discovery: **ARP-10/#105 CLOSED**, [Research integration 45190975614](https://github.com/fjure/agentic-trading-platform/actions/runs/45190975614) und [Contracts 45190976026](https://github.com/fjure/agentic-trading-platform/actions/runs/45190976026) erfolgreich auf der veröffentlichten Nachfolgebasis. Die früheren Hinweise auf offenes ARP-10 und rotes Baseline-Contractsgate sind damit historische Befunde, keine aktuellen IEF-Blocker. Die ARP-10-Abhängigkeitskanten in IEF-04/06/09 bleiben erfüllte Provenienzvoraussetzungen.

`design.md` D7, Vertragspräzisierung, `agent-task-model.md`, `discovery.md`, `test-strategy.md` und IEF-04 bewahren die entscheidende Grenze: ARP-10 belegt technische Offline-/CI-Readiness, **keine reale Provider-/Modell-/Credential-/Tarif-/Zweck- oder Fachfreigabe**. Der neu geplante `PORTFOLIO_EXPLANATION_V1` bleibt gesondert zu implementieren, zu prüfen und zu autorisieren; `RealProviderVerified` bleibt laut ARP-Evidenz Nein. IEF benötigt weiterhin eigene grüne Nachweise auf seinem späteren Implementierungs-SHA.

Ergebnis der Delta-Nachprüfung: keine neuen offenen P1-/P2-Architekturbefunde; die veröffentlichte ARP-10-Erweiterung ist mit dem geplanten vorhandenen Supervisor, der lokalen Analysequelle und den additiven IEF-Verträgen vereinbar. Dies ist kein unabhängiger Vollreview sämtlicher ARP-10-Fachlogik und keine Wiederholung ihrer CI-Abnahme.
