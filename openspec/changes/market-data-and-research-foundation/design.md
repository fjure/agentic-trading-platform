# Design

## Context

Stand 25.10.2026, **Planung, keine neue Anwendung implementiert**. Motivation: [Proposal](proposal.md). Kanonischer Produktkontext: [Vision](../../../docs/vision/product-vision.md). Ausgangscode und Grenzen: [Bestandsaufnahme](../../../docs/evidence/market-research-discovery.md).

`MarketBar` besitzt Decimal-/UTC-/Verfügbarkeitsfelder; `AlpacaMarketDataReader` liefert begrenzte aktuelle Sitzungsevidenz. `services/worker/jobs.py` implementiert SQL-Leases/Fencing. HTTPX, SQLAlchemy und Alembic sind bereits gepinnt. Alpaca-Credentials und Egress gehören ausschließlich Execution; Worker darf Jev erreichen, nicht Alpaca. Bestehende unveränderliche Tradingtabellen und frische `MarketState`-Referenzen bleiben unangetastet. Python 3.13, PostgreSQL, Angular Material 22 und OpenAPI 3.1.2 werden weiterverwendet.

## Goals / Non-Goals

**Goals:** Ein realer, begrenzter Aktien-/ETF-Historienpfad mit dauerhafter Speicherung, konservativer PIT-Selektion, ResearchEvidence, MarketContext, API und kleiner UI; vollständig offline zertifizierbar.

**Non-Goals:** Backtestingengine, Portfolio-/Strategielernen, Multi-Agent-Orchestrierung, News-/Fundamental-/Makroadapter, vollwertige Corporate-Action-Ingestion, alle Assetklassen, Tradingintegration von Researchdaten oder neue Handelsfreigaben. LangGraph wird nicht eingeführt: heutige Aufgaben sind deterministische Jobs; fachliche Ports halten spätere Orchestrierung offen.

## Decisions

### 1. Module in vorhandenen Prozessen, alleiniger Alpaca-Zugriff durch Execution

Neue Domain-/Querymodule unter `packages/research/`; kein neuer Microservice und kein TimescaleDB. Ein separater Research-SQLbereich in der normalen Labdatenbank hält alle Handelsledger unverändert. Researchjobs sind auch bei DISABLED nutzbar, öffnen aber keine Tradinggates. Ein ausschließlich GET-fähiger Historienadapter unter `packages/brokers/` wird nur durch `services/execution/research_ingestion.py` konsumiert. Credentials kommen aus dem bestehenden geschützten Execution-Setup und werden nie weitergereicht. Die credentialfreie Verarbeitung bekommt normalisierte Seiten, keine HTTPclients oder Rohantworten. Ohne eingerichtete Credentials: `BLOCKED_CREDENTIALS`, kein Testschlüssel.

Execution verarbeitet höchstens eine begrenzte Historienseite je Researchtakt mit separatem Verbindungs-/Zeitbudget; Reconciliation, Kill und Pause haben Priorität. Initial: ein aktiver Job, maximal 1 Research-GET/s, Timeout 5s, Antwort 2MiB, maximal 1000 Bars/Seite, 1000 Seiten/Job, drei Wiederholungen pro Seite, hartes Laufzeitbudget 15min. Zusätzlich gilt ein gemeinsamer account-/hostgebundener Limiter für historische und bestehende Datenrequests: maximal das kleinere aus bestätigtem Entitlement und 120 GET/min, davon höchstens 60/min für Research; Research erhält bei unklarer Restkapazität oder Safetyarbeit keinen Slot. 429/Retry-After sperrt neue Researchrequests hostweit; bestehende konservative Tradingfehlergates bleiben erhalten. Nichtblockierender I/O und getrennte Pools müssen durch einen Last-/Latenztest für Kill/Pause/Reconciliation nachgewiesen werden. Bei Erschöpfung `PARTIAL` mit gespeichertem Cursor, explizite Wiederaufnahme statt angeblicher Vollständigkeit. Keine Änderung der Order-/Cancelrouten. Alternatives direktes Worker-Alpaca-API oder neuer Schlüsselbesitzer würde AGENTS verletzen und wird verworfen.

### 2. Kleine echte Providerfähigkeit

Erster realer Adapter: Alpaca `/v2/stocks/bars`, Instrumente SPY/QQQ, Intervalle 15Min und 1Day, USD, explizit konfiguriertes IEX und ausschließlich `adjustment=raw`. Datenfeed wird niemals implizit gewechselt. Anfragen partitionieren Symbolgültigkeiten; UTC-Zeiträume sind intern halboffen `[start,end)`, inklusive Providergrenzen werden lokal gefiltert. Limit 366 Tage je APIauftrag. Crypto hat Domainfähigkeit, aber zunächst keinen zertifizierten historischen Adapter; Forex/Derivate ebenfalls nicht.

Aktuelle Tradingreader-Zeitfenster und `_MAX_PAGES` werden nicht erweitert. Dessen sichere Parsing-/Hostregeln werden nach Prüfung als kleine GET-Helfer wiederverwendet, ohne Tradingtests zu lockern. Öffentliche Dokumentation belegt Pagination und Entitlementfehler, aber keine historische Originalveröffentlichung jeder Kerzenrevision. Deshalb keine rückdatierte Verfügbarkeit. Vor jedem Provider-I/O muss die lokale Providerpolicy den beabsichtigten lokalen Datengebrauch als bestätigt ausweisen; `license_status=UNKNOWN/BLOCKED` ist standardmäßig gesperrt und wird weder aus HTTP200 noch aus vorhandenen Credentials abgeleitet. Betreiberbestätigung, Policyversion und Verwendungsumfang sind ohne Vertragsinhalte/PII auditierbar; keine fremden Rechte automatisch annehmen. Bei 401/403 blockieren; 429/5xx/Timeout dauerhaft `RETRY_WAIT`, begrenztes exponentielles Backoff (1/2/4s) mit injizierbarer Uhr/Zufall und gedeckeltem Retry-After (60s); keine aktive Schlafschleife im Tradingtakt. Wiederholte Tokens abbrechen. Privates Token niemals in API/Logs; bei ungültigem Cursor aktuelle Partition von vorn dedupliziert lesen.

### 3. Persistenz, Identität und Wiederaufnahme

Additive Alembic-Migrationen im bestehenden Pfad, Researchtabellen im getrennten `research`-Schema:

| Tabelle | Wesentliche Identität/Inhalt |
|---|---|
| instruments / symbol_mappings | stabile UUID, Assetklasse, MIC/Handelsplatz, Basis-/Quoteeinheit, Currency, optionale Kontraktmetadaten; Provider/Symbol und gültiges Zeitintervall ohne Überlappung |
| bar_revisions | Instrument, Provider, Feed, Intervall, Start/Ende, Adjustierung; OHLCV NUMERIC, Quellrevision falls vorhanden, lokaler Revisioncounter, Vorgänger, Inhaltsfingerprint, Event/Published/Available/Ingested UTC |
| evidence | immutable ID, Typ, Referenzen, normalisierter versionierter Inhalt, Qualität, Konfidenz getrennt, Quellreferenz ohne Credentialquery, Inhaltsfingerprint |
| ingestion_jobs / page_receipts | deduplizierte Request-ID und Payloadhash, Partition/Cursor, Status, Attempts, Retryzeit, Owner/Fencingtoken, Counts, Trace-ID; private Cursor nur im internen Datensatz |
| calendar_versions / coverage | versionierte Sessionbelege und Herkunft, erwartete/fehlende/ungeprüfte Bereiche, Qualitätsregelversion |

Natürlicher Barkey enthält Feed und Adjustierung. Nutzerpräzisierung 25.10.2026 im Apply: Wiederholung des aktuellen Inhaltsstands dedupliziert; veränderte Werte erzeugen unter Keylock eine neue lokale Revision. Bei A → B → A entsteht eine dritte Revision mit dem ursprünglichen Inhaltsfingerprint, eigener Revisionsidentität und Vorgänger B. Inhaltsfingerprints sind innerhalb eines natürlichen Keys daher nicht global eindeutig. Bestätigte Seitenwiederholungen werden über ihre unveränderlichen Seitenreceipts dedupliziert und dürfen nachfolgende Revisionen nicht zurücksetzen. Derselbe deklarierte Providerrevisionkey mit anderem Inhalt ist ein Konflikt und wird quarantänisiert. Fingerprint = SHA256 kanonischer UTF-8-Inhaltsdarstellung mit Schema, Instrument, Source, Feed, Zeiten, Adjustierung und Decimalstrings; lokale Empfangszeit und zufällige IDs gehören nicht zum Inhaltsfingerprint. Die unveränderliche Evidenzidentität bindet natürliche Identität, Inhaltsfingerprint und Revisionsvorkommen; neue Beobachtungen ersetzen keine alte Ingestionzeit. Revisionsidentitäten hängen deterministisch von natürlichem Key, Inhaltsfingerprint und lokalem Revisionscounter ab. Der Inhaltsfingerprint enthält weder Revisionscounter noch Vorgänger und bleibt bei einer Rückkorrektur gleich.

Normalisierte Seite, Evidenz, Qualitätsbefunde und Fortschritt committen atomar, nur mit gültiger Lease/Fencingbindung. Übernahme prüft gespeicherten Fortschritt. Ein inkrementeller Updatejob überlappt ein konfiguriertes abgeschlossenes Intervall, um Korrekturen zu entdecken; beliebig alte Revisionen verlangen expliziten Backfill. Vollständigkeit wird nie aus „HTTP 200“ abgeleitet.

Control liest redigierte Views und darf ausschließlich validierte Jobanforderungen anlegen; Execution darf Researchjobs claimen und Researchdaten appendieren. Worker liest Researchdaten, erhält keine Alpaca-Netzrechte oder Handelsschreibrechte. Keine UPDATE/DELETE-Rechte auf Evidenz/Bars und keine DDL in Runtime-Rollen. Bestehende Jobmechanik als Muster nutzen, ihre tradingbezogene Retry-/Recoverysemantik nicht pauschal erweitern.

### 4. Explizite Zeitsemantik statt behaupteter historischer Wahrheit

Erste zertifizierte Semantik heißt `observed-as-of`. Anfrage enthält `valuation_at`, `knowledge_at` (beide UTC, knowledge darf valuation nicht überschreiten) und gebundenen `snapshot_at`. Auswahl verlangt `bar_end/event_time <= valuation_at`, `available_at <= knowledge_at`, `ingested_at <= knowledge_at` und Snapshotgrenze; je natürlichem Key neueste bis dahin bekannte gültige Revision. Bei unbewiesener Providerpublikation `published_at=null`, `availability_basis=FIRST_OBSERVED`, `available_at=ingested_at`. Neuere Backfills sind für frühere Wissensstände somit leer. Ein Ereigniszeitverlauf mit heutigem Wissensstand ist als heutige Sicht abfragbar, aber kein historischer Backtestbeleg.

Für stabile mehrseitige Abfragen reicht `snapshot_at` allein nicht: Eine noch offene Ingestiontransaktion könnte später committen. Jede Researchschreibtransaktion reserviert daher unter einem bis Commit gehaltenen globalen Researchversionslock eine monotone `commit_version`; der Querybeginn liest die letzte committed Version. Der Cursor bindet dieses High-water-Mark und den Filterhash. Alle gelesenen Revisionen/Qualitäts-/Kalenderbelege erfüllen zusätzlich `commit_version <= snapshot_version`. Eine Transaktion, die nach Querybeginn committen kann, besitzt zwingend eine höhere Version. Der Versionslock wird ausschließlich für die kurze normalisierte SQLmutation nach abgeschlossenem Provider-I/O mit maximal 1s Lockwartezeit erworben; niemals über Netzwerkaufrufe oder Retrywarten halten. Locktimeout führt zu sicherer Jobwiederaufnahme. Eine bloße SQLsequence oder Transaktionsstartzeit ohne Commitserialisierung genügt nicht. Gezielter Zwei-Verbindungen-Test: spätes Commit zwischen Seite1 und2 darf den Snapshot nicht verändern.

`asof` beim Provider ist Symbolmapping, kein Vintagenachweis. Adjustierte Preise werden zunächst ausdrücklich nicht ingestiert; Metadaten und Ablehnungsstatus schaffen Erweiterbarkeit. Spätere Vintage-/Corporate-Action-Provider benötigen einen eigenen Change mit beweisbarer Veröffentlichungs-/Faktorenhistorie. Ein dokumentierter Originalzeitstempel allein entfernt niemals den lokalen Ingestionfilter. Das konservative Modell verliert historische Abdeckung, verhindert aber unbewiesenes Zukunftswissen.

### 5. Qualität, Kalender und Context

Historische Kalender werden ausschließlich über den bestehenden erlaubten Executionhost `https://paper-api.alpaca.markets/v2/calendar` begrenzt und versioniert geladen; Datum, Sessiongrenzen, Endpunktversion und Quellenbeleg persistieren. Unterstützte Kalenderhorizonte werden nicht aus einem anderen Alpaca-Produkt übernommen: Broker-API-`/v1/calendar` oder neue `/v3`-Verträge sind kein Nachweis für diesen Pfad. Außerhalb tatsächlich belegter Responsecoverage gilt `UNKNOWN_CALENDAR`, auch nach 2029. Leere Antworten allein beweisen keine dauerhafte Feiertagscoverage. Der statische 2026-Paperkalender bleibt unverändert eine Tradingfixture. 15m-Bars nur reguläre Sitzungen, 1Day nach Sessionende, Feiertage und frühe Schlüsse beachten. Kein Auffüllen fehlender Kerzen und kein Wissen um Delistings behaupten. IEX-Lücken sind nicht automatisch Providerfehler; Qualitätsbefund trennt `MISSING_EXPECTED`, `NO_OBSERVATION` und `UNKNOWN_CALENDAR`.

Getrennte Ports `HistoricalBarsSource`, `NewsSource`, `FundamentalsSource`, `MacroEventsSource` mit typisierten Requests, Seiten, Capabilities und stabilen Fehlercodes. Nur HistoricalBarsSource besitzt in diesem Change einen echten Adapter. Die anderen Ports und Offline-Normalisierungsfixtures sind explizit `NOT_IMPLEMENTED`. ResearchEvidence unterstützt BAR/NEWS/FUNDAMENTAL/MACRO_EVENT/DERIVED, payloadabhängig typisiert; kein freies Provider-JSON in öffentlichen DTOs.

MarketContext aggregiert gültige Evidence-IDs in demselben Snapshot. Preisänderung = `(last_close / first_close - 1)` über das angeforderte Fenster; Volatilität = nicht annualisierte Populationsstandardabweichung der einfachen Renditen, Decimal-sqrt unter festem Rechenkontext/Algorithmusversion; mindestens drei aufeinanderfolgende Bars und zwei Renditen. Fehlendes erwartetes Fenster oder uneinheitliche Intervalle ergibt `NOT_COMPUTABLE`. Kalender-/Feedqualität wird mitgeführt. Kein modellbasiertes Regime, keine erfundenen News oder Sentimentwerte.

### 6. Additive Verträge und kleine Oberfläche

Im Apply zuerst kanonische OpenAPI-/Schemas-/Beispiele, danach reproduzierbar generierter Client und echte Routen. Geplante Operationen unter `/api/v1/research`: GET `/sources`, GET `/bars`, GET `/evidence/{id}`, GET `/context`, POST `/ingestion-jobs`, GET `/ingestion-jobs`, GET `/ingestion-jobs/{id}`. Barsantwort enthält Coverage/Qualität; alle Listen maximal 200 Ergebnisse, stabiler Cursor an Filterhash, Wissensstand und Snapshotversion gebunden. Defaults/UTC-Präzision und Fehlercodes werden in MDR-01 verbindlich festgelegt. Mutation nur mit LocalSession, bestehendem Same-Origin-/CSRF-Schutz und Idempotency-Key, Antwort 202 mit dauerhafter Job-ID; Statuslesung löst keine Providerabfrage aus. Keine freien URLs, Schlüssel oder privaten Seitentoken in DTOs.

Material-Researchbereich im bestehenden Supervisor mit Quellenstatus, Zeitraum/Instrument, ausdrücklich sichtbarem Wissensstand, Tabelle/kleinem Kursverlauf, Evidenzdetails und reloadfähigen Jobzuständen. Explizites Importkommando startet Datenerhebung, keinen Handel. Bestehende SSE muss nicht erweitert werden; begrenztes Polling für aktive Jobs genügt.

### 7. Provider-Matrix und Quellenentscheidung

Offizielle Quellen geprüft am 25.10.2026; Angaben sind Planungsgrundlage, keine erworbenen Zugangs- oder Nutzungsrechte. Zahlen und Lizenzfreigaben vor tatsächlicher Nutzung erneut prüfen. Kein Scraping, kein neues Abonnement, keine Datenweitergabe.

| Quelle / Fähigkeit | Historie und Aktualität | Kosten / Limits / Rechte | Qualität / Entscheidung |
|---|---|---|---|
| Alpaca Bars | Aktien-/ETF-Historie; Feed-/Planabhängigkeit, kein garantierter Originalvintage | Basic laut [Planübersicht](https://docs.alpaca.markets/us/docs/about-market-data-api) kostenlos, 200 Historienaufrufe/min; intern deutlich niedriger; Speicherung/Nutzung vom persönlichen Vertrag abhängig | [Barsvertrag](https://docs.alpaca.markets/us/reference/stockbars) mit Pagination; echter Adapter jetzt, IEX nicht als Gesamtmarkt behandeln |
| Alpaca/Benzinga News | [Dokumentation](https://docs.alpaca.markets/us/docs/historical-news-data) nennt historische News seit 2015; Updates sind nicht automatisch ursprüngliche Publikation | Entitlement, Nachrichtenrechte, Speicherung und aktuelle Limits nicht verifiziert; vor Integration zu klären | Getrennter Newsport jetzt, echter Adapter später |
| SEC EDGAR Fundamentals | [Submissions/Companyfacts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) und Filingbezug; Amendments/vintages fachlich nötig | Öffentliche APIs; aktuelle Fair-Access-/Identifikationsregeln und Datenrechte vor Folgechange prüfen; kein zugesagtes SLA | Verlässliche Primärfilings, Taxonomie-/Einheitenkonflikte; nur Port jetzt |
| FRED/ALFRED Makro | [Realtime-Perioden](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html), historische Wissensstände und Revisionen | APIkey nötig; variable Nutzungsgrenzen, Serienrechte gemäß [Terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html); keine pauschale Weitergabelizenz | Für Vintagefolgechange geeignet; nicht tagesgenaue Vintages als Intraday-Veröffentlichung ausgeben; nur Port jetzt |
| Wirtschaftskalender / Alternative Daten | Noch kein ausgewählter Anbieter | Historische Releasezeiten, Lizenz und Limits unbewiesen | `NOT_IMPLEMENTED`, Folgechange; Alpaca-Sitzungskalender ist kein Makroereigniskalender |

## Risks / Trade-offs

- [Historienzugang oder Rechte fehlen] → echter Adapter offline prüfen, tatsächlichen Zugang `UNVERIFIED`/`BLOCKED` dokumentieren; kein Integrationsabschluss durch Dokumentation ersetzen.
- [Backfill suggeriert PIT] → FIRST_OBSERVED und doppelte Available-/Ingestedgrenze; historische Queries dürfen leer sein.
- [Datenrevisionen/Survivorship] → append-only Vintages, zeitgebundene Symbole; begrenztes Universum ausdrücklich keine survivorshipfreie Marktstudie.
- [Research belastet Execution] → ein Job, getrennte Budgets, Priorität für Safety, Fail-Closed-Lasttests.
- [Researchrollenmigration weitet Rechte aus] → tatsächliche SQL-/Deploymentnegativen für alle bestehenden Rollen; getrennte Views und minimale Grants.
- [Neue Routen kollidieren mit exaktem Vertragsgleichheitstest] → MDR-01 bleibt bis gemeinsamer Runtime-/Contractabnahme offen; keine Stubs, keine Testlockerung, noch kein main-Push.

## Migration Plan

Erst Vertragsbasis und eigenständige Researchmigrationen auf Issuebranch; anschließend echter offline getesteter vertikaler Pfad und gemeinsame Abnahme vor Integration nach main. Migrationsstand vor/nach prüfen, aktuelle Ledgercounts/Hashes erhalten, Idempotenz und Least Privilege gegen reale Test-PG prüfen. Keine realen Datenbank-/Rollenänderungen in der Planungsphase. Runbooks nennen alle neuen nicht geheimen Limits/Schalter; `RESEARCH_INGESTION_ENABLED=false` als Default, expliziter lokaler Importauftrag und private Executioneinrichtung erforderlich. Rücknahme deaktiviert Dispatch/APIfunktion und bewahrt Researchdaten; kein destruktives Downgrade oder Volume-Reset. Alte Anwendung muss additive Migration tolerieren. Archiv erst nach vollständigem Apply, Tests, Review und echter Issue-Synchronität.
