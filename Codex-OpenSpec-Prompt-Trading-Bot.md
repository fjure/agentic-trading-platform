# Codex\-Master\-Prompt: Agentic Trading Lab mit OpenSpec

Den folgenden Prompt vollständig in Codex im Root\-Verzeichnis des vorgesehenen Repositories einfügen\.

---

## Prompt für Codex

Du arbeitest im aktuellen Repository an einem lokal betriebenen Multi\-Agent\-Trading\-System\. Richte das Projekt spec\-driven mit **OpenSpec** ein und erstelle zunächst eine vollständige, prüfbare Spezifikation für den ersten vertikalen Durchstich\. In diesem Durchlauf darfst du Infrastruktur für OpenSpec, Repository\-Skills, die freigegebenen Codex\-MCPs und reine Dokumentationsartefakte anlegen, aber **noch keinen Anwendungscode implementieren**\. Die in diesem Prompt verlangte Tooling\-Installation ist ausdrücklich autorisiert; Sicherheits\-, Berechtigungs\- und Approval\-Grenzen der Umgebung bleiben maßgeblich\.

### Arbeitsweise

1. Untersuche zuerst das Repository vollständig:
   - Lies vorhandene `AGENTS.md`\-Dateien und weitere Projektanweisungen\.
   - Prüfe Repository\-Status, vorhandene Dateien, Tool\-Versionen und bestehende Architektur\.
   - Falls das Verzeichnis leer ist, behandle es als Greenfield\-Projekt namens `agentic-trading-lab`\.
   - Erzeuge kein verschachteltes zweites Git\-Repository\.
2. Richte OpenSpec anhand der **aktuellen offiziellen Dokumentation** für Codex ein:
   - Prüfe zuerst, ob OpenSpec bereits installiert und initialisiert ist\.
   - Falls erforderlich, installiere die aktuelle stabile Version von `@fission-ai/openspec` und führe `openspec init` für Codex aus\.
   - Aktiviere das erweiterte Profil mit mindestens `explore`, `propose`, `apply`, `verify`, `sync` und `archive` und führe anschließend `openspec update` aus\.
   - Verwende die von OpenSpec für Codex erzeugten Skills unter `.agents/skills/`\.
   - Deaktiviere OpenSpec\-Telemetrie für dieses Projekt beziehungsweise die aktuelle Umgebung\.
   - Halte die tatsächlich verwendete OpenSpec\-Version in den Projektunterlagen fest\.
3. Führe anschließend den Abschnitt **Codex\-Tooling\-Bootstrap** vollständig aus\. Repository\-Skills und MCP\-Konfiguration zählen in diesem Durchlauf als Entwicklungsinfrastruktur, nicht als Anwendungscode\.
4. Verwende OpenSpec als Source of Truth:
   - Change\-ID: `paper-trading-vertical-slice`
   - Erstelle mindestens `proposal.md`, Delta\-Spezifikationen, `design.md` und `tasks.md`\.
   - Anforderungen müssen mit eindeutigen IDs und testbaren Akzeptanzszenarien formuliert sein\.
   - Architekturentscheidungen müssen mit Begründung, Alternativen und Konsequenzen dokumentiert werden\.
   - Tasks müssen klein, abhängigkeitsgeordnet, eindeutig abnehmbar und auf konkrete Pfade bezogen sein\.
   - Pflege eine Traceability\-Matrix: Requirement → Designentscheidung → Task → Test\.
   - Wenn OpenSpec eigene Konventionen vorgibt, folge ihnen und ergänze die benötigten Inhalte innerhalb dieser Struktur\.
5. Stelle nur Fragen, wenn eine Antwort die Spezifikation fundamental verändert und keine sichere Annahme möglich ist\. Dokumentiere sonst die Annahme und arbeite weiter\. Implementiere keine Vermutung als versteckte fachliche Entscheidung\.
6. Prüfe zeitabhängige technische Aussagen in aktuellen Primärquellen\. Verwende für Codex, MCP\-Server, Jev, Alpaca, OpenSpec, LangGraph, Angular, Python\-Bibliotheken und Docker ausschließlich offizielle Dokumentation beziehungsweise offizielle Repositories\. Vermerke Abrufdatum, relevante Einschränkungen und die Version, gegen die später implementiert werden soll\.

### Codex\-Tooling\-Bootstrap

Richte Codex für dieses Repository reproduzierbar und mit minimalen Berechtigungen ein\. Prüfe vor jeder Installation die aktuelle offizielle Codex\-Dokumentation sowie das offizielle Repository beziehungsweise die offizielle Registry des jeweiligen Anbieters\. Verwende keine inoffiziellen Forks, Wrapper oder Installationsskripte\. Installiere nur stabile Versionen, pinne die tatsächlich verwendeten Versionen, soweit der jeweilige Installationsweg dies unterstützt, und dokumentiere Quelle, Version, Scope und Verifikationskommando unter `docs/development/codex-tooling.md`\.

#### Repository\-Skills

Verwende die durch OpenSpec erzeugten Skills und lege zusätzlich folgende projektspezifische Skills unter `.agents/skills/<skill-name>/SKILL.md` an:

1. `api-contract-workflow`
   - aktiviert sich bei Änderungen an HTTP\-, SSE\-, OpenAPI\- oder JSON\-Schema\-Verträgen;
   - erzwingt die Reihenfolge Spec → OpenAPI/Schema → Lint/Validierung → Breaking\-Change\-Prüfung → FastAPI\-Adapter → Angular\-Clientgenerierung → Contract\-Tests;
   - untersagt manuelle Änderungen an generiertem Clientcode;
   - verwendet später die im Repository festgelegten deterministischen CLI\-Werkzeuge, keinen OpenAPI\-MCP\.
2. `trading-safety-review`
   - aktiviert sich bei Änderungen an Risk, Execution, Broker, Betriebsmodus, Secrets, Orders, Reconciliation oder Deployment;
   - prüft mindestens: kein direkter Brokerzugriff durch Agenten, deterministische Risk\-Freigabe, `LIVE` nicht erreichbar, Default fail\-closed, `Decimal`, UTC, Kill\-Switch, Secret\-Grenzen und vollständige Auditierbarkeit;
   - liefert eine nachvollziehbare Checkliste mit Befund, Evidenz und blockierenden Abweichungen\.
3. `broker-adapter-certification`
   - aktiviert sich bei neuen oder geänderten Brokeradaptern;
   - prüft Capability Matrix, FakeBroker\-Vertrag, Idempotenz, Timeout, Rate Limit, Partial Fill, Cancel/Fill\-Rennen, unbekannten Brokerstatus, Reconnect und Reconciliation;
   - verbietet eine Freigabe allein aufgrund eines erfolgreichen Happy Paths\.
4. `decision-engine-evaluation`
   - aktiviert sich bei Änderungen an Jev, Prompts, Questions, DecisionEngine\-Adaptern oder Entscheidungslogik;
   - vergleicht versionierte Jev\-Konfigurationen mit Mock und deterministischer Baseline über Replay und Shadow Mode;
   - bewertet Schemaeinhaltung, Abstention Rate, Konfidenzkalibrierung, Latenz, Kosten und Stabilität;
   - interpretiert Confidence niemals als Gewinnwahrscheinlichkeit und verwendet kurzfristige Paper\-P&L nicht als alleinige Freigabegrundlage\.
5. `github-issue-sync`
   - aktiviert sich nach Änderungen an OpenSpec\-`tasks.md`, Requirement\-Zuordnungen, Abhängigkeiten oder Taskstatus;
   - synchronisiert OpenSpec\-Tasks idempotent über den offiziellen GitHub MCP mit GitHub Issues;
   - behandelt OpenSpec als Source of Truth für Scope, Akzeptanzkriterien, Abhängigkeiten und Zielpfade, während GitHub Issues die ausführbare Arbeits\- und Statusansicht bilden;
   - erkennt und meldet Abweichungen, erzeugt keine Duplikate und überschreibt keine manuellen Inhalte außerhalb des verwalteten Bereichs\.

Jeder Skill benötigt eine präzise YAML\-Frontmatter mit `name` und einer triggerfähigen `description`, klare Ein\- und Ausschlussbedingungen, benötigte Eingaben, Ablauf, Sicherheitsgrenzen und erwartete Evidenz\. Vermeide die Duplikation umfangreicher OpenSpec\-Inhalte oder dauerhafter Regeln aus `AGENTS.md`; Skills beschreiben wiederholbare Arbeitsabläufe\. Lege Scripts oder Templates nur an, wenn sie den Ablauf deterministischer machen\. Skill\-Scripts sind Tooling und dürfen in diesem Durchlauf angelegt werden, aber sie dürfen keine Trading\-, Broker\- oder Anwendungslogik implementieren\.

Validiere nach der Anlage, dass Codex alle OpenSpec\- und Projekt\-Skills erkennt\. Falls die aktuelle Sitzung neue Skills erst nach einem Neustart laden kann, dokumentiere dies, führe alle möglichen statischen Prüfungen aus und nenne den notwendigen Neustart als klaren nächsten Schritt\.

#### MCP\-Server

Prüfe zuerst mit `codex mcp list` und der aktuellen Codex\-Konfiguration, welche Server bereits vorhanden sind\. Erzeuge keine Duplikate und überschreibe keine bestehende kompatible Konfiguration\. Installiere beziehungsweise konfiguriere anschließend über die von Codex offiziell unterstützten Befehle oder Konfigurationsschlüssel:

1. **Context7** — standardmäßig installieren
   - verwende ausschließlich den offiziellen Upstash\-Context7\-MCP;
   - pinne die verifizierte stabile Paketversion, falls der Installationsweg dies zulässt;
   - beschränke die Toolfreigabe auf Dokumentationssuche und Library\-Auflösung;
   - verwende zunächst den Betrieb ohne API\-Key, sofern er offiziell unterstützt wird; fordere bei Rate Limits einen Key an, übernimm ihn aber niemals in Repository, Prompt, Log oder Klartextkonfiguration\.
2. **Playwright MCP** — standardmäßig installieren
   - verwende ausschließlich den offiziellen Microsoft\-Playwright\-MCP;
   - pinne die verifizierte stabile Version;
   - verwende ein isoliertes Browserprofil, sofern die aktuelle Version dies unterstützt, und greife niemals auf ein persönliches Browserprofil, gespeicherte Logins oder Brokerkonten zu;
   - nutze den MCP für interaktive Dashboard\-Inspektion, Screenshots, Accessibility und Fehlersuche\. Reproduzierbare End\-to\-End\-Tests bleiben normale Playwright\-Testdateien und laufen über CLI/CI\.
3. **GitHub MCP** — verpflichtend installieren
   - verwende ausschließlich den offiziellen GitHub MCP Server;
   - verifiziere den GitHub\-Remote und das Zielrepository eindeutig\. Falls noch kein GitHub\-Remote existiert, ist der Repository\-Owner beziehungsweise die Ziel\-URL eine blockierende Eingabe\. Erstelle niemals eigenmächtig ein öffentliches Repository; ein neues privates Repository darf nur nach ausdrücklicher Autorisierung erstellt werden;
   - aktiviere lesende Werkzeuge für Repository, Issues, Pull Requests und Actions sowie ausschließlich die für das Erstellen und Aktualisieren von Issues und Labels erforderlichen Schreibwerkzeuge\. Andere schreibende GitHub\-Werkzeuge bleiben deaktiviert;
   - konfiguriere Issue\-Mutationen mindestens approval\-pflichtig beziehungsweise mit dem von der aktuellen Codex\-Version vorgesehenen `writes`\-Modus\. Dieser Prompt autorisiert die nachfolgend definierte Issue\-Synchronisierung, nicht jedoch beliebige Repository\- oder Workflow\-Änderungen;
   - wenn OAuth oder eine andere interaktive Anmeldung nötig ist, gib das exakte offizielle Login\-Kommando aus und pausiere den Durchlauf an dieser Stelle\. Arbeite nicht ohne GitHub MCP weiter und erfinde oder extrahiere keine Credentials\.
4. **Grafana MCP** — jetzt nicht installieren
   - dokumentiere ihn als späteren optionalen Schritt, sobald eine reale Grafana\-Instanz und ein separater read\-only Service Account existieren;
   - keine vorbereitenden Secrets und keine Schreibrechte einrichten\.

Nutze die aktuellen Codex\-Möglichkeiten für `enabled_tools`, `disabled_tools` und Approval\-Modi, um die jeweils kleinste Toolmenge freizugeben\. Verifiziere die resultierende Konfiguration mit `codex mcp list` und, soweit ohne Neustart möglich, mit einem ungefährlichen Read\-only\-Smoke\-Test je installiertem Server\. Wenn ein Neustart von Codex erforderlich ist, dokumentiere den Zustand eindeutig und versuche nicht, eine zweite Codex\-Sitzung rekursiv zu starten\.

Installiere oder konfiguriere ausdrücklich **keinen** Alpaca\-, IBKR\-, CCXT\-, Broker\-, Order\-, Jev\-, PostgreSQL\-, Filesystem\-, Shell\-, eigenständigen Git\-, Docker\-, Memory\- oder generischen Reasoning\-MCP\. Der verpflichtende offizielle GitHub MCP ist davon ausdrücklich ausgenommen\. Alpaca und Jev werden später ausschließlich durch die Anwendung hinter typisierten Ports angesprochen\. Codex erhält niemals Broker\-Credentials, Orderberechtigungen oder Zugriff auf reale Trading\-Endpunkte\. Ein OpenAPI\-MCP ist nicht erforderlich; OpenAPI wird über versionierte Dateien und deterministische CLI\-/CI\-Werkzeuge bearbeitet\.

Halte in `docs/development/codex-tooling.md` mindestens fest:

- installierte und bewusst nicht installierte Skills/MCPs;
- offizielle Quelle und Abrufdatum;
- gepinnte Version und Konfigurationsscope;
- aktivierte Tools und Approval\-Regeln;
- Verifikationsresultat und eventuell erforderlicher Neustart/Login;
- Update\-, Disable\- und Uninstall\-Kommandos;
- Aussage, dass keine Secrets im Repository oder in der Dokumentation gespeichert wurden\.

#### Verpflichtende Synchronisierung mit GitHub Issues

Nach erfolgreicher OpenSpec\-Validierung und vor dem Abschluss dieses Durchlaufs synchronisierst du alle implementierbaren Tasks aus `paper-trading-vertical-slice` über den offiziellen GitHub MCP in das verifizierte Zielrepository\. Die Synchronisierung ist eine ausdrücklich autorisierte externe Schreiboperation, bleibt aber an die Approval\-Regeln der Umgebung gebunden\.

Verwende folgende Regeln:

- OpenSpec\-Requirements, `design.md` und `tasks.md` bleiben die fachliche Source of Truth\. GitHub Issues spiegeln Planung und Ausführungsstatus und dürfen keine abweichenden Anforderungen etablieren\.
- Erzeuge genau ein Issue je implementierbarem Leaf\-Task\. Titel folgen dem Muster `[<Task-ID>] <kurzer Titel>`\.
- Jedes Issue enthält einen stabilen unsichtbaren Marker wie `<!-- openspec-task-id: <Task-ID> -->` und einen klar markierten, von Codex verwalteten Inhaltsblock\.
- Der verwaltete Block enthält mindestens Change\-ID, Task\-ID, Requirement\-IDs, Zielpfade, Abhängigkeiten, erwartetes Verhalten, Akzeptanzkriterien, Testevidenz und Definition of Done\.
- Verwende mindestens die Labels `openspec` und `change:paper-trading-vertical-slice` sowie sinnvolle vorhandene Komponenten\- oder Statuslabels\. Erzeuge fehlende Labels nur, wenn dafür das eng begrenzte GitHub\-MCP\-Werkzeug freigegeben ist\.
- Verwende nach Möglichkeit einen Milestone `paper-trading-vertical-slice`\. Falls der installierte GitHub MCP Milestones nicht sicher unterstützt, dokumentiere die Einschränkung und verwende ausschließlich das Change\-Label; dies blockiert nicht die übrige Issue\-Synchronisierung\.
- Löse Abhängigkeiten über verlinkte Issue\-Nummern und eine maschinenlesbare `Depends on`\-Sektion auf\. Behaupte keine native GitHub\-Abhängigkeit, wenn die verfügbare API sie nicht unterstützt\.
- Suche vor jeder Erstellung anhand des stabilen Markers, der Task\-ID und der persistenten Zuordnung nach einem bestehenden Issue\. Aktualisiere passende Issues idempotent und erzeuge keine Duplikate\.
- Verändere ausschließlich Titel, Labels und den markierten verwalteten Block\. Erhalte manuelle Ergänzungen, Kommentare und Inhalte außerhalb dieses Blocks\.
- Schließe ein Issue erst, wenn der zugehörige OpenSpec\-Task im selben Arbeitsstand als abgeschlossen markiert ist, die verlangte Evidenz vorliegt und die relevanten Tests erfolgreich sind\. Eine Issue\-Schließung allein darf keinen OpenSpec\-Task als erledigt markieren\.
- Bei widersprüchlichem Status, gelöschten Issues, fehlender Berechtigung oder unklarer Zuordnung führe keine destruktive Reparatur aus\. Dokumentiere den Drift und behandle ihn als blockierenden Sync\-Fehler\.
- Speichere die Zuordnung versioniert unter `docs/development/github-issue-sync.json` mit mindestens `schemaVersion`, Repository, Change\-ID, Task\-ID, Issue\-Nummer, Issue\-URL, letztem synchronisierten Inhalts\-Hash und Synchronisierungszeitpunkt\. Speichere keine Tokens oder sonstigen Credentials\.
- Führe nach dem Schreiben einen Read\-back über den GitHub MCP aus und verifiziere Anzahl, Marker, Titel, Labels, Zuordnungen und Abhängigkeiten\. Die reine erfolgreiche API\-Antwort genügt nicht als Nachweis\.

Die Definition of Done jedes späteren OpenSpec\-Apply\-Durchlaufs umfasst die Aktualisierung der zugehörigen Issues\. Änderungen an `tasks.md` müssen im selben Arbeitsdurchlauf über `$github-issue-sync` abgeglichen werden\. Kann der verpflichtende GitHub\-Sync wegen fehlender Authentifizierung, Berechtigung oder Zielrepository nicht ausgeführt werden, ist die Definition of Ready nicht erfüllt: Stoppe mit einer präzisen Blockerbeschreibung und dem kleinsten erforderlichen Nutzer\-Schritt\.

### Produktziel

Das langfristige Ziel ist ein lokal gehosteter Multi\-Agent\-Trading\-Bot mit drei dauerhaft verfügbaren logischen Agentenrollen:

1. **Market Intelligence Agent**
   - verarbeitet später Marktdaten, News, Unternehmensmeldungen, Makroereignisse und wissenschaftliche Papers;
   - erzeugt strukturierte `EvidenceBundle`\-Objekte mit Quellen, Zeitstempeln, Instrumentbezug, Aktualität, Konflikten und Qualitätsmerkmalen\.
2. **Quant Research Agent**
   - übersetzt Hypothesen und Papers in reproduzierbare Experimente;
   - erzeugt versionierte `CandidateStrategy`\-Ergebnisse mit Daten\-, Code\-, Prompt\- und Modellversion sowie Out\-of\-Sample\-Metriken\.
3. **Portfolio Planner Agent**
   - kombiniert ausschließlich freigegebene Strategien, Marktfeatures, Evidenz und Portfoliozustand;
   - erzeugt strukturierte `TradeProposal`\-Objekte oder enthält sich explizit\.

Agenten dürfen niemals direkt Orders senden\. Nur der Execution\-Service besitzt Paper\- beziehungsweise spätere Broker\-Credentials\. Jede Order muss durch eine deterministische Risk Engine geprüft und freigegeben werden\.

### Ziel des ersten OpenSpec\-Changes

Spezifiziere einen kleinen, aber echten vertikalen Durchstich:

```text
Alpaca-Marktdaten
  → normalisierter MarketState
  → Jev Decision Engine
  → TradeProposal
  → deterministische Risk Engine
  → Alpaca Paper Order
  → Execution Report / Reconciliation
  → Supervisor-Dashboard
```

Dieser Change soll die Architekturgrenzen und den vollständigen Datenfluss beweisen\. Umfang:

- ausschließlich Paper Trading;
- ein Alpaca\-Paper\-only\-Konto;
- Instrumentuniversum zunächst konfigurierbar und standardmäßig klein, beispielsweise `SPY`, `QQQ` und `BTC/USD`, soweit die aktuell verifizierte Alpaca\-Paper\-API diese Symbole unterstützt;
- Zeithorizont zunächst 15\-Minuten\-Kerzen;
- Aktienentscheidungen nur auf abgeschlossenen Kerzen innerhalb einer explizit definierten Session;
- Kryptowährung darf rund um die Uhr bewertet werden;
- `LONG` und `FLAT` als erste Zielpositionen;
- keine Shorts, Optionen, Futures, Hebelprodukte oder echte Geldbewegungen in diesem Change;
- keine autonome Strategieänderung;
- keine automatische Promotion von Paper zu Live;
- keine produktive News\- oder Paper\-Pipeline im ersten Change; dafür zunächst ein typisierter, austauschbarer `EvidenceProvider` mit neutralem beziehungsweise simuliertem EvidenceBundle;
- ein minimales Supervisor\-Dashboard, das Entscheidungen nachvollziehbar macht und den Paper\-Handel pausieren kann\.

### Verbindliche Architekturentscheidungen

Plane folgende Zieltechnologien, sofern eine aktuelle Primärquelle keinen harten technischen Widerspruch zeigt:

- Python 3\.13 Backend;
- FastAPI und Pydantic für HTTP\-API und Verträge;
- PostgreSQL mit SQLAlchemy und Alembic;
- PostgreSQL\-Jobtabelle mit Lease, Heartbeat, Retrylimit und Dead\-Letter\-Status;
- transaktionale Outbox für dauerhafte Ereignisse;
- LangGraph nur für zustandsbehaftete Agentenworkflows, mit persistentem PostgreSQL\-Checkpointer;
- Angular mit TypeScript und RxJS für das Dashboard;
- **Angular Material** als kostenfreie, Open\-Source\-UI\-Library unter MIT\-Lizenz für Tabellen, Formulare, Dialoge, Statusanzeigen, Navigation und responsive Layouts\. PrimeNG darf nicht verwendet werden\. Prüfe die aktuell unterstützte Angular\-Material\-Version anhand der offiziellen Angular\-Dokumentation und halte sie in den Projektunterlagen fest;
- Docker Compose für den lokalen Linux\-Betrieb;
- strukturierte Logs, OpenTelemetry\-Metriken und Health\-Endpunkte;
- UTC für technische Zeitstempel und `Decimal` für Geld, Preise und Mengen im transaktionalen Kern\.
- Für gemeinsam genutzten Frontend\-Zustand darf ein Store Manager eingesetzt werden\. Bevorzugt ist **NgRx Store** beziehungsweise NgRx SignalStore, sofern die aktuelle Angular\-Version dies unterstützt\. Verwende einen Store nur für tatsächlich geteilten, langlebigen oder reaktiv benötigten Zustand, beispielsweise Systemmodus, Portfolio\-Snapshot, Verbindungsstatus, Cursor und Dashboard\-Filter\. Lokaler UI\-Zustand bleibt in Angular\-Komponenten oder Services\. Dokumentiere die Wahl, Version, Zuständigkeiten, Selektoren, Actions beziehungsweise Events und Persistenzgrenzen\. Der Store darf keine Risk\- oder Brokerlogik enthalten und ist nicht Source of Truth für transaktionale Daten; diese bleiben im Backend und in PostgreSQL\.

Halte das MVP als modularen Monolithen beziehungsweise wenige getrennte Prozesse\. Vermeide Kubernetes, Kafka, Microservice\-Zerlegung und zusätzliche Infrastruktur ohne belegte Notwendigkeit\.

### API\- und Datenverträge

Arbeite für die Control API **contract\-first**\. OpenSpec beschreibt fachliches Verhalten und Architektur; eine versionierte OpenAPI\-Datei ist der ausführbare HTTP\-Vertrag zwischen FastAPI und Angular\.

- Lege den kanonischen Vertrag unter `contracts/http/control-api.openapi.yaml` ab\.
- Prüfe anhand aktueller Primärquellen und der konkret gewählten Toolversionen, ob FastAPI, Validatoren und der Angular\-Codegenerator OpenAPI 3\.2\.1 vollständig unterstützen\. Verwende andernfalls OpenAPI 3\.1\.2 und dokumentiere die Abweichung als ADR\. Pinne die gewählte Version; ein späteres Upgrade ist ein eigener OpenSpec\-Change\.
- Erstelle beziehungsweise ändere den OpenAPI\-Vertrag zusammen mit Specs und `design.md`, bevor die zugehörigen Endpunkte implementiert werden\.
- Generiere aus OpenAPI einen typisierten Angular\-Client, bevorzugt mit OpenAPI Generator `typescript-angular`, sofern die aktuelle Version mit dem gewählten OAS\-Stand kompatibel ist\. Lege generierten Code in einem klar gekennzeichneten Pfad ab und bearbeite ihn niemals manuell\.
- Generiere keinen FastAPI\-Serverstub\. Implementiere die Transportadapter bewusst gegen den Vertrag und halte Transportmodelle von Domainmodellen getrennt\.
- Verwende `/api/v1` als HTTP\-Prefix\.
- Schreibende Operationen verwenden einen `Idempotency-Key`; wiederholte Requests liefern semantisch dasselbe Ergebnis, solange der Schlüssel gültig ist\.
- Verwende W3C Trace Context beziehungsweise `traceparent` für die durchgängige Korrelation; die fachliche Trace\-ID bleibt in Audit\- und Entscheidungsdaten sichtbar\.
- Fehler verwenden RFC 9457 `application/problem+json` sowie stabile, maschinenlesbare `code`\-Werte und enthalten keine Secrets oder internen Stacktraces\.
- Übertrage Geldwerte, Preise und Mengen als Dezimal\-Strings; technische Zeitstempel als ISO\-8601 in UTC\.
- Listen verwenden Cursor\-Pagination\. Risk\-Policy\- und Konfigurationsänderungen verwenden Optimistic Locking über Version oder `ETag`/`If-Match`\.
- Beschreibe den SSE\-Endpunkt einschließlich Eventformat, Heartbeat, Cursor, Reconnect und Fehlerverhalten im OpenAPI\-Vertrag\. Verwende Snapshot plus Cursor, damit Reconnects keine stillen Lücken erzeugen\.
- Hinterlege für handels\- und sicherheitsrelevante Operationen Beispiele für Erfolg, fachliche Ablehnung, Versionskonflikt, temporären Ausfall und unbekannten Brokerstatus\.
- Exponiere weder rohe Alpaca\- noch rohe Jev\-Payloads an das Dashboard\. Öffentliche DTOs sind stabile interne Verträge des Systems\.

Definiere wiederverwendbare Payloads und interne Events als versionierte JSON\-Schemas unter `contracts/schemas/`\. Jedes Event enthält mindestens `eventId`, versionierten `eventType`, `occurredAt`, `aggregateId`, `aggregateVersion`, `traceId` und `payload`\. Verwende AsyncAPI erst, wenn mehrere unabhängig betriebene Producer oder Consumer, externe Consumer oder ein Message Broker vorhanden sind\. Dokumentiere diese Einführungsschwelle als ADR; im ersten Change genügen JSON\-Schemas und die transaktionale Outbox\.

Die CI muss:

- OpenAPI und JSON\-Schemas validieren und linten;
- Breaking Changes gegenüber dem Hauptbranch erkennen;
- den Angular\-Client reproduzierbar neu generieren und unerwarteten Drift ablehnen;
- FastAPI per Contract\- und schema\-basierten Negativtests gegen den Vertrag prüfen;
- Beispiele aus den Verträgen validieren;
- sicherstellen, dass Spec, Vertrag, Implementierung und generierter Client gemeinsam geändert werden\.

### Betriebsmodi, Replay und Brokerfähigkeiten

Modelliere die Betriebsmodi explizit als `DISABLED`, `REPLAY`, `SHADOW` und `PAPER`\. `LIVE` ist im ersten Change weder konfigurierbar noch über einen versteckten Schalter erreichbar\. `SHADOW` erzeugt und bewertet Entscheidungen sowie theoretische Orders, ruft aber niemals `submit_order` auf\.

Jeder Brokeradapter liefert eine typisierte Capability Matrix, beispielsweise für Market\-/Limit\-Orders, Fractional Quantity, Short Selling, Bracket Orders, Paper Trading und unterstützte Sessions\. Fachlogik darf keine nicht bestätigte Fähigkeit voraussetzen\.

Speichere den normalisierten, unveränderlichen Input jedes Entscheidungslaufs so, dass er deterministisch erneut gegen `MockDecisionEngine`, `DeterministicBaselineDecisionEngine` und versionierte Jev\-Konfigurationen abgespielt werden kann\. Der Replay\-Pfad darf keinen Brokerzugriff besitzen\.

### Austauschbare Ports

Die Fachlogik darf weder von Alpaca noch von Jev direkt abhängen\. Spezifiziere mindestens folgende Ports und Adapter:

```text
MarketDataProvider
BrokerAdapter
DecisionEngine
EvidenceProvider
Clock
IdGenerator
EventPublisher
```

Geplante Implementierungen:

```text
AlpacaMarketDataProvider
AlpacaPaperBrokerAdapter
JevDecisionEngine
MockDecisionEngine
DeterministicBaselineDecisionEngine
NeutralEvidenceProvider
FakeBrokerAdapter
```

Der Alpaca\-Adapter muss so gekapselt sein, dass später ein `IbkrBrokerAdapter` und ein CCXT\-basierter Krypto\-Adapter ergänzt werden können, ohne die Domain\-Logik umzubauen\.

### Jev als Decision Layer

Jev ist ein austauschbarer, externer Decision\-Service und nicht die Risk Engine\.

Spezifiziere einen strukturierten `MarketState` mit mindestens:

- Instrument\-ID und Markt;
- `asOf`, `eventTime`, `receivedAt` und `availableAt`;
- Timeframe;
- geschlossene Kurskerze und ausgewählte Features;
- Spread beziehungsweise vorhandene Daten zur Ausführbarkeit;
- Datenalter und Qualitätsflags;
- aktuelles Portfolio und vorhandene Position;
- EvidenceBundle mit Aktualität und Konfliktmerkmalen\.

Jev soll zunächst mehrere atomare Fragen in einem Request beantworten:

- Marktregime als `Choice`, einschließlich `uncertain`;
- Evidenzqualität als `Score`;
- wesentlicher Signalkonflikt als `Noul`;
- optional eine Shadow\-Entscheidung `LONG | FLAT | ABSTAIN`, die zunächst getrennt ausgewertet werden kann\.

Jede Entscheidung speichert:

- vollständige typisierte Antwort;
- Wahrscheinlichkeitsverteilungen und Confidence;
- Modellkennung;
- Prompt\-/Question\-Version;
- Input\-Hash;
- Latenz und API\-Verbrauch;
- Trace\-ID;
- Gültigkeitsende\.

Jev\-Confidence darf im ersten Change weder als Gewinnwahrscheinlichkeit interpretiert noch direkt zur Positionsgrößenbestimmung verwendet werden\. Bei Timeout, Schemafehler, niedriger Confidence, veralteten Daten oder fehlenden Pflichtfeldern muss das Ergebnis `ABSTAIN` beziehungsweise keine neue Position sein\. Ein Jev\-Ausfall darf Risk Engine, Reconciliation und Exit\-/Pause\-Funktionen nicht blockieren\.

### Deterministische Risk Engine

Alle Regeln werden als normaler, testbarer Code ausgeführt\. Die Spezifikation muss folgende Invarianten enthalten:

- Default\-Betriebsmodus ist `DISABLED`\. Der Übergang nach `PAPER` muss explizit, auditiert und nur nach erfolgreichem Health\- und Reconciliation\-Check erfolgen; nach einem unsicheren Neustart gilt mindestens `PAUSE_NEW`;
- jede Order referenziert exakt ein unveränderliches TradeProposal und eine RiskDecision;
- Vorschläge haben eine kurze Gültigkeitsdauer und werden direkt vor dem Submit erneut geprüft;
- Risiko berücksichtigt Positionen, offene Orders und bereits reserviertes Risiko;
- parallele Vorschläge dürfen dasselbe Budget nicht doppelt verbrauchen;
- bei unbekanntem Brokerstatus bleibt die Reservierung bestehen;
- nach einem Submit\-Timeout wird nicht blind erneut gesendet;
- zuerst erfolgt Broker\-Reconciliation anhand interner und brokerseitiger IDs;
- Ledger\-Abweichungen blockieren neue Einstiege;
- Kill\-Switch und `PAUSE_NEW` funktionieren ohne Jev;
- Risk\-Limits sind konfigurierbar, versioniert und im Dashboard sichtbar;
- Testwerte sind keine Anlageempfehlung und werden klar als Simulationswerte gekennzeichnet\.

Definiere konservative Paper\-Defaults nur für die technische Simulation, beispielsweise:

- höchstens eine offene Position je Instrument;
- höchstens zwei offene Positionen insgesamt;
- maximal 10 % Paper\-Equity Notional je Position;
- maximales tägliches Paper\-Verlustlimit von 1 %;
- keine neue Position bei fehlender oder veralteter Marktinformation;
- keine Positionsvergrößerung bei ungeklärter Order oder Reconciliation\-Abweichung\.

Alle Werte müssen über validierte Konfiguration änderbar sein\. Änderungen werden auditiert\.

### Order\- und Ledger\-Modell

Spezifiziere mindestens:

```text
Instrument
MarketBar
EvidenceBundle
MarketState
DecisionResult
TradeProposal
RiskPolicy
RiskReservation
RiskDecision
OrderIntent
BrokerOrder
ExecutionReport
Position
PortfolioSnapshot
AgentRun
AuditEvent
```

Orderstatus mindestens:

```text
CREATED
RISK_RESERVED
SUBMITTING
ACKNOWLEDGED
PARTIALLY_FILLED
FILLED
REJECTED
CANCEL_PENDING
CANCELED
UNKNOWN
```

Dokumentiere Zustandsübergänge, Idempotenzschlüssel, Unique Constraints, Cancel/Fill\-Rennen, Teilfüllungen, Reconnects und Wiederanlauf nach Prozessabbruch\. Beanspruche kein End\-to\-End\-Exactly\-Once\. Definiere stattdessen mindestens einmal zugestellte Ereignisse und idempotente Consumer\.

### Supervisor\-Dashboard

Der erste Dashboard\-Scope umfasst:

- Systemmodus und Paper\-Konto;
- Broker\-, Daten\- und Jev\-Verbindungszustand;
- aktueller Portfolio\-Snapshot;
- Agent\-/Worker\-Status und Heartbeats;
- letzte MarketStates;
- Jev\-Antworten einschließlich Confidence und Verteilung;
- TradeProposals mit Begründungsdaten;
- RiskDecisions mit maschinenlesbaren Reason Codes;
- Orders, Teilfüllungen und Reconciliation\-Status;
- Audit\-Timeline anhand der Trace\-ID;
- aktuelle API\-/Tokenkosten, soweit verfügbar;
- Aktion `PAUSE_NEW`;
- Aktion zum Fortsetzen nur nach erfolgreichem Health\- und Reconciliation\-Check\.

Verwende Snapshot plus Cursor für Reconnects und SSE oder eine begründete Alternative für Live\-Updates\. Das Dashboard darf keine internen Chain\-of\-Thought\-Inhalte voraussetzen\. Zeige stattdessen strukturierte Eingaben, Ergebnisse, Quellen, Reason Codes und Zustandsübergänge\.

Verwende Angular Material für die UI\-Bausteine und NgRx beziehungsweise NgRx SignalStore nur für gemeinsam genutzten reaktiven Zustand\. Dokumentiere, welche Daten aus dem Backend stammen, welche im Store gehalten werden und welche ausschließlich lokaler Komponentenstatus sind\. Der Store darf keine geheimen Daten, Broker\-Credentials oder unverarbeiteten sensiblen Payloads persistieren\.

### Security

Spezifiziere mindestens:

- Paper\- und spätere Live\-Credentials strikt getrennt;
- Alpaca\- und Jev\-Schlüssel niemals in Git, Logs, Prompts, Browser oder Dashboard;
- Secrets als restriktiv gemountete Dateien oder gleichwertige lokale Secret\-Lösung;
- nur der Execution\-Prozess erhält Broker\-Credentials;
- Dashboard standardmäßig nur an Loopback gebunden;
- Eingaben von News/PDFs später grundsätzlich unvertrauenswürdig;
- keine Toolausführung aus externen Dokumentinhalten;
- keine dynamische Installation unbekannter Pakete durch Agenten im laufenden Betrieb;
- Abhängigkeiten pinnen, Secret\-Scan und Dependency\-Scan;
- Audit\-Events append\-only aus Sicht der Anwendung;
- Logs mit Redaction\-Test;
- reale Order\-Endpunkte im ersten Change technisch nicht konfiguriert beziehungsweise durch einen harten Guard blockiert\.

### Teststrategie

Erzeuge eine konkrete Testpyramide und ordne jeden Test Requirements zu:

1. Unit\- und Property\-Tests
   - Geld\-/Mengenrundung;
   - Zeit\- und Datenalterregeln;
   - Risk\-Limits;
   - atomare Reservierung unter Parallelität;
   - alle Orderzustandsübergänge;
   - Jev\-Fallbacks und `ABSTAIN`\.
2. Contract\-Tests
   - OpenAPI\-Validierung und Vertragsbeispiele;
   - FastAPI\-Verhalten gegen `control-api.openapi.yaml`;
   - schema\-basierte Negativ\- und Fuzz\-Tests;
   - Breaking\-Change\-Erkennung;
   - reproduzierbare Angular\-Clientgenerierung ohne Drift;
   - `DecisionEngine` gegen Mock und Jev\-Schema;
   - `BrokerAdapter` gegen FakeBroker und Alpaca\-Paper\-Vertrag;
   - Markt\- und Instrumentsymbole;
   - Fehler\-, Rate\-Limit\- und Timeout\-Mapping\.
3. Integrations\- und Recovery\-Tests
   - PostgreSQL, Migrationen, Job\-Leases und Outbox;
   - Prozessabbruch zwischen Broker\-Submit und lokalem Commit;
   - doppelte sowie ungeordnet eintreffende Brokerereignisse;
   - Teilfüllung und Cancel/Fill\-Rennen;
   - Reconnect und Reconciliation;
   - Dashboard\-Reconnect\.
4. Paper\-Smoke\-Test
   - opt\-in, niemals verpflichtend für normale CI;
   - prüft Paper\-Credentials, Marktdaten, minimale Paper\-Order, Status und Storno beziehungsweise Fill;
   - darf ausschließlich gegen den verifizierten Paper\-Endpunkt laufen;
   - muss bei fehlendem explizitem Paper\-Flag abbrechen\.
5. Security\- und Failure\-Injection\-Tests
   - Secret\-Redaction;
   - Prompt\-Injection als reine Datenlast;
   - Jev\-Ausfall;
   - Alpaca\-Ausfall;
   - Datenstillstand;
   - DB\-Neustart;
   - Kill\-Switch ohne Dashboard\.

Definiere messbare Abnahmekriterien\. Beispiele: keine Doppelorder in den simulierten Recovery\-Szenarien, vollständige Traceability eines Paper\-Trades, keine Broker\-Credentials außerhalb des Execution\-Prozesses und sichere Enthaltung bei unvollständigen Daten\.

### Repository\-Zielstruktur

Bewerte und konkretisiere folgende Struktur, ohne leere Architektur nur um der Architektur willen anzulegen:

```text
apps/dashboard/
services/control_api/
services/worker/
services/execution/
services/watchdog/
packages/domain/
packages/risk/
packages/brokers/
packages/decisions/
packages/research/
packages/observability/
contracts/http/
contracts/schemas/
contracts/examples/
config/markets/
config/risk/
config/agents/
tests/unit/
tests/property/
tests/contracts/
tests/integration/
tests/recovery/
tests/security/
infra/compose/
docs/adr/
docs/runbooks/
```

Begründe, falls du Pfade zusammenfasst oder verschiebst\.

### AGENTS\.md

Falls noch keine geeignete Root\-`AGENTS.md` existiert, erstelle eine kurze Datei mit dauerhaft gültigen Projektregeln\. Sie soll keine OpenSpec\-Artefakte duplizieren\. Nimm mindestens auf:

- OpenSpec\-Spezifikationen sind die fachliche Source of Truth\.
- Vor jeder wesentlichen Änderung ist ein OpenSpec\-Change anzulegen oder zu aktualisieren\.
- Live Trading bleibt deaktiviert, bis ein eigener späterer Change ausdrücklich freigegeben wurde\.
- Agenten dürfen keine Brokerorders senden\.
- Risk Engine und Execution\-Gates bleiben deterministisch\.
- Geldwerte verwenden Decimal; technische Zeitstempel verwenden UTC\.
- Credentials und personenbezogene Daten dürfen nicht in Repository, Logs oder Modellkontext gelangen\.
- Tests und Reconciliation sind Teil der Definition of Done\.
- Aktive Spezifikation und Implementierung müssen gemeinsam aktualisiert werden\.
- PrimeNG ist nicht zulässig; für das Dashboard wird Angular Material verwendet\.
- Gemeinsamer Frontend\-Zustand wird nur bei begründetem Bedarf über NgRx beziehungsweise NgRx SignalStore verwaltet\.
- OpenSpec beschreibt fachliches Verhalten; `contracts/http/control-api.openapi.yaml` ist der kanonische HTTP\-Vertrag\.
- Generierter API\-Clientcode wird niemals manuell geändert\.
- Breaking API Changes, Contract Drift und ungültige Vertragsbeispiele blockieren die Definition of Done\.
- Repository\-Skills beschreiben wiederholbare Arbeitsabläufe; dauerhafte Regeln bleiben in `AGENTS.md` und change\-spezifische Anforderungen in OpenSpec\.
- Codex\-MCPs erhalten nur die minimal erforderlichen Tools und Berechtigungen\. Broker\-, Order\-, Jev\- und produktive Datenbankzugriffe werden Codex nicht als MCP bereitgestellt\.
- MCP\- oder Skill\-Konfigurationen dürfen keine Secrets enthalten und werden bei sicherheitsrelevanten Änderungen mitgeprüft\.
- Der offizielle GitHub MCP und die idempotente Synchronisierung von OpenSpec\-Tasks mit GitHub Issues sind verpflichtend\. OpenSpec bleibt Source of Truth; Issue\-Drift blockiert die Definition of Done\.

### Inhalt der OpenSpec\-Artefakte

Die erstellten Artefakte müssen mindestens enthalten:

- klare Ziele und Nicht\-Ziele;
- Systemkontext und Trust Boundaries;
- Komponenten\- und Sequenzdiagramme in Mermaid;
- fachliche Anforderungen mit Given/When/Then\-Szenarien;
- Datenmodell und Zustandsmaschinen;
- interne API\-/Event\-Verträge und Fehlersemantik;
- versionierten OpenAPI\-Vertrag, JSON\-Schemas, Beispiele und die begründete OAS\-Versionsentscheidung;
- Betriebsmodi `DISABLED`, `REPLAY`, `SHADOW` und `PAPER`;
- Broker\-Capability\-Matrix und deterministisches Replay\-Konzept;
- Entscheidung zu synchronen und asynchronen Grenzen;
- Idempotenz\- und Reconciliation\-Konzept;
- Sicherheits\- und Threat\-Modell;
- Observability\- und Audit\-Konzept;
- lokale Betriebs\- und Recovery\-Runbooks;
- Teststrategie und Abnahmekriterien;
- versionierte Konfigurationsstrategie;
- Frontend\-Architektur mit Angular Material und gegebenenfalls NgRx beziehungsweise NgRx SignalStore;
- bekannte Risiken und offene Punkte;
- bewusst verschobene spätere Changes:
  - produktive News\-/Paper\-Ingestion;
  - vollständige drei Agentenworkflows;
  - historische Replay\-/Walk\-forward\-Pipeline;
  - IBKR\-Paper\-Adapter;
  - CCXT\-Sandbox\-Adapter;
  - begrenzter Live\-Pilot;
  - autonome Strategie\-Promotion\.

### Qualitätsregeln für tasks\.md

Jeder Task muss enthalten:

- stabile Task\-ID;
- zugehörige Requirement\-IDs;
- konkrete Zielpfade;
- Abhängigkeiten;
- erwartetes Verhalten;
- passenden Test oder andere überprüfbare Evidenz;
- klaren Abschlusszustand\.

Ordne die Arbeit in vertikale, integrierbare Pakete\. Der erste implementierbare Meilenstein muss vollständig lokal mit FakeBroker und MockDecisionEngine funktionieren\. Erst danach folgen Jev und Alpaca Paper\. Plane insbesondere:

1. Repository\- und Build\-Grundlage;
2. Domain\-Verträge und Datenmodell;
3. Persistenz, Audit, Jobs und Outbox;
4. OpenAPI\-/JSON\-Schema\-Verträge, Contract\-Validierung und generierter Angular\-Client;
5. FakeBroker, MockDecisionEngine und Risk Engine;
6. vollständiger lokaler Fake\-Trade mit Recovery und deterministischem Replay;
7. Jev\-Adapter und Shadow\-Modus;
8. Alpaca\-Marktdaten und Capability Matrix;
9. Alpaca\-Paper\-Ausführung;
10. Dashboard und Kontrollpfade mit Angular Material sowie gegebenenfalls NgRx;
11. Failure Injection, Paper\-Smoke\-Test und abschließende Verifikation\.

Parallelisierbare Tasks dürfen nur als solche markiert werden, wenn sie keine gemeinsamen Dateien oder unaufgelösten Verträge teilen\.

### Definition of Ready für die Implementierung

Bevor du diesen Durchlauf abschließt, verifiziere:

- alle OpenSpec\-Artefakte sind syntaktisch valide;
- alle Requirements besitzen Akzeptanzszenarien;
- jede sicherheits\- oder geldrelevante Invariante ist mindestens einem Test zugeordnet;
- alle externen APIs sind hinter Ports gekapselt;
- OpenAPI\-, JSON\-Schema\- und Beispielartefakte sind valide und die Codegenerator\-Kompatibilität ist dokumentiert;
- die Traceability umfasst Requirements, OpenAPI\-Operationen beziehungsweise Eventschemas, Tasks und Tests;
- der generierte Angular\-Client ist reproduzierbar und driftfrei;
- Live Trading ist außerhalb des Scopes und technisch geblockt;
- PrimeNG wurde nicht als Abhängigkeit eingeplant oder installiert;
- Angular Material ist als kostenfreie Open\-Source\-UI\-Library dokumentiert;
- die Entscheidung für oder gegen NgRx beziehungsweise NgRx SignalStore ist begründet;
- die Tasks sind vollständig abhängigkeitsgeordnet;
- offene Fragen sind als blocking oder non\-blocking klassifiziert;
- Annahmen sind sichtbar dokumentiert;
- die OpenSpec\-Skills sowie `api-contract-workflow`, `trading-safety-review`, `broker-adapter-certification`, `decision-engine-evaluation` und `github-issue-sync` sind angelegt und statisch geprüft;
- Context7 und Playwright MCP sind über offizielle Quellen eingerichtet oder ein konkreter, begründeter technischer Blocker ist dokumentiert;
- der offizielle GitHub MCP ist installiert, authentifiziert, auf das eindeutig verifizierte Zielrepository begrenzt und für die erforderlichen Issue\-Operationen funktionsfähig;
- jeder implementierbare OpenSpec\-Task besitzt genau ein verifiziertes GitHub Issue mit stabilem Marker, Labels, Requirement\-Zuordnung, Abhängigkeiten, Akzeptanzkriterien und Testevidenz;
- `docs/development/github-issue-sync.json` ist vollständig, duplikatfrei und durch einen Read\-back gegen GitHub verifiziert;
- fehlender GitHub\-Remote, fehlende Authentifizierung, unzureichende Issue\-Berechtigung oder nicht auflösbarer Sync\-Drift werden als blockierend behandelt und nicht übersprungen;
- `codex mcp list`, Tool\-Allowlisten und Approval\-Regeln wurden geprüft;
- kein Broker\-, Order\-, Jev\-, PostgreSQL\-, Filesystem\-, Shell\-, Git\-, Docker\-, Memory\- oder Reasoning\-MCP wurde eingerichtet;
- `docs/development/codex-tooling.md` enthält Versionen, Quellen, Scope, Verifikation sowie Update\-/Disable\-/Uninstall\-Hinweise und keine Secrets;
- keine Anwendungsimplementierung wurde erzeugt; zulässig sind nur die ausdrücklich genannten OpenSpec\-, Skill\-, MCP\- und Dokumentationsartefakte\.

### Abschluss dieses Durchlaufs

Führe die OpenSpec\-Validierung aus und behebe alle festgestellten Probleme\. Antworte danach mit:

1. der erzeugten Change\-ID;
2. Links beziehungsweise Pfaden zu `proposal.md`, Specs, `design.md`, `tasks.md` und `AGENTS.md`;
3. den wichtigsten Architekturentscheidungen;
4. der Auswahl und Begründung für Angular Material sowie gegebenenfalls NgRx beziehungsweise NgRx SignalStore;
5. der gewählten OpenAPI\-Version, dem Codegenerator und dem Ergebnis der Contract\-Validierung;
6. den getroffenen Annahmen;
7. höchstens fünf tatsächlich relevante offene Punkte;
8. dem Ergebnis der OpenSpec\-Validierung;
9. einer Tabelle aller angelegten Skills und MCPs mit Version, Scope, aktivierten Tools, Verifikationsstatus und gegebenenfalls notwendigem Neustart oder Login;
10. dem verifizierten GitHub\-Repository, Milestone beziehungsweise Change\-Label, der Anzahl synchronisierter Issues und dem Pfad zu `docs/development/github-issue-sync.json`;
11. dem exakten nächsten Codex\-Kommando zum Neustarten, Prüfen beziehungsweise Implementieren\.

**Stoppe anschließend\. Implementiere in diesem Durchlauf keinen Anwendungscode\.**

---

## Empfohlener Folgeablauf in Codex

OpenSpec nennt für Codex die Skills nach dem Muster `$openspec-<aktion>`\. Die tatsächlich installierten Namen nach `openspec init` sind maßgeblich\.

1. Falls der Entwurf fachlich angepasst werden soll:
   ```text
   Aktualisiere den OpenSpec-Change paper-trading-vertical-slice wie folgt: <Änderungen>. Validieren, aber noch nicht implementieren.
   ```
2. Vor der Implementierung:
   ```text
   $openspec-verify paper-trading-vertical-slice
   ```
3. Implementierung:
   ```text
   $openspec-apply paper-trading-vertical-slice
   ```
4. Nach abgeschlossener Implementierung:
   ```text
   $openspec-verify paper-trading-vertical-slice
   ```
5. Nach Review und erfolgreicher Verifikation:
   ```text
   $openspec-sync paper-trading-vertical-slice
   $openspec-archive paper-trading-vertical-slice
   ```

Wenn die installierte OpenSpec\-Version andere Argumente oder Skillnamen ausgibt, sind deren aktuelle Hinweise zu verwenden\.
