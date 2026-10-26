# Backtesting-Implementierungsnachweis

Stand 26.10.2026: technische Offlineabnahme bestanden. Der nachvollziehbare Integrations- und Issueabschluss steht in `github-backtesting-git-integrations.json`, `github-backtesting-issue-sync.json` und den OpenSpec-Checkboxen. Keine Archivierung, Providerfreigabe oder Handelsaktivierung.

## BKT-01

Fünf additive Operationen, 48 Backtestschemas und 17 Fixtures implementiert. 173 Vertragsprüfungen (Backtest und bestehendes Research) bestanden. `validate-contracts`: 58 Operationen, 27 Schemas, 70 Antwortbeispiele; Redocly 2.62.1 bestanden. `oasdiff` 1.33.0 gegen tatsächliches main `5d65820` bestanden. OpenAPI Generator 7.26.0 erzeugte zweimal 208 bytegleiche Dateien; TypeScript 6.0.2/Angular 22.2.2 Compile bestanden. Quellreview: geschlossene DTOwurzeln, kanonische Decimal-/UTCwerte, zusätzliche Operationen ohne Änderung bestehender Routen. Konditionale ResearchMetric-Zweige enthalten explizite additionalProperties:true für den gepinnten Linter; die DTOwurzel bleibt geschlossen, Negativtest bestanden.

Dies belegt die Vertragsform. Persistenz, historische Semantik, Cursorsignatur und Browser wurden gesondert in den nachstehenden Paketen geprüft.

## BKT-02

Unveränderliche Experiment-/Fold-/Manifestwerte, geschlossenes Strategieregister, exakte Decimalvalidierung und kanonische Eingabehashs implementiert. 29 Modell-/Strategie-/Requestprüfungen bestanden: unbekannte Programme/Versionen, SMAgrenzen, überlappende/Zukunftsfenster, Nachnullnormalisierung und UTCpräzision. Modelle/Strategien haben keinen Broker-/Netzwerkport. Kanonische Requests werden vor Jobanlage geprüft; freie Manifestreferenzen bleiben abgewiesen. Alle Domain-/Simulator-/Evaluationprüfungen zusammen bestanden vor der Integration ebenfalls (42 plus neue Requestfälle).

## BKT-03

Acht Dataset-Unitfälle einschließlich 258-Schritt-Fensterparität bestanden. Echter PostgreSQL-Test erneut bestanden (26.98s): Snapshot bei offenem Writer, spätere Commits ausgeschlossen; 1er-Keysetseiten mit gleichem Barende/lokaler Revision und unterschiedlicher natürlicher Identität; 76 tatsächliche Einzelzeitpunktqueryvergleiche; Intradayplan, DST/Earlyclose und spätere Kalenderkorrektur; 60s-Signal vs spätere 300s-Ergebnisrevision; unveränderlicher Coveragebeleg ohne Runtime-Schreibrecht. Reader begrenzen Metadaten auf 100001 Zeilen und scheitern bei mehr als 100000, Strategieinputs bleiben auf höchstens slow Bars begrenzt. Bestehende Researchselektion unverändert. Additive Coverage-Migration0028 und exaktes Paper-ACLmanifest für0028/29 ergänzen keine Finanzrechte.

## BKT-04

Neun Simulator-/Propertyprüfungen bestanden (einschließlich 80 Hypothesisfällen). Unabhängige Kauf-/Verkaufsrechnungen bestätigen Gebühren, Slippage, Mengenraster und Cash; Nullmenge, unfinanzierbare Verkaufsgebühr, Overnight, fehlendes Fillziel und Fensterende geprüft. Jede Bar ruft einen Budgetcheckpoint auf. Festes Decimalmodell precision38/ROUND_HALF_EVEN und nächste kalenderzulässige Kerze; Ergebnisbars gelangen nicht in Strategieinputs. Keine Brokerimporte oder Brokeraktionen.

## BKT-05

Vier zusätzliche Evaluation-/Kennzahlprüfungen bestanden. Folds starten mit eigenem Cash und flacher Position; feste Strategieparameter und identische Kosten für flat/buy-hold. Handrechnungen für Nettorendite, Turnover und Drawdown einschließlich Startcash; Gebühren und Slippage getrennt. BLOCKED bewahrt abgeschlossene Foldresultate und erzeugt keine verkettete Gesamtrendite. Deterministischer Warm-up wird durch BKT-03 gebunden und durch die Domainmanifestregeln geprüft.

## BKT-06

Additive Strategierevisionen/Manifeste/Jobs/Folds/Resultate und append-only Audit in backtest, eigene Workerrolle ohne Finanzrechte, privates persistentes Cursorsignaturmaterial nur APIlesbar. Echter PG-Job-/Rollentest: Idempotenz409, Queue100, ein aktiver Job, Lease/Fenceübernahme, alte Manifest-/Checkpoint-/Publishversuche abgewiesen, Crashrollback, drei Attempts und unveränderliche Resultate. Vollständiger Workercrash vor Veröffentlichung wurde im Offline-Acceptancetest mit exakt gleichem Manifest und Resultat nach Übernahme reproduziert.

Nach Review: Providerselektion und Kalenderbindung bei nicht baraligned Start präzisiert; nun 14 Dataset-Unitfälle plus echter PG-Test bestanden (27.16s). Buildfingerprint bindet tatsächliche Quellen/Lock; Worker weist abweichende Jobversion vor Datensicht ab. Unit-/PG-Regressionen für Quellen-/Lockwechsel, Defaultdisabled und Versionsmismatch: 11 bestanden (57.91s). Separates Containerimage gebaut, ohne Netzwerk/private Mounts tatsächlichen Codefingerprint geprüft. Öffentliche Fillprojektion mit Context38 und 12MiB-Persistenzbudget: zwei zusätzliche Unitfälle bestanden. CPUarbeit hält keine SQLtransaktion; Checkpoints, feste Attempts und Rollen sind Teil der Laufzeit.

## BKT-07

Fünf authentifizierte additive Operationen, strikte DTOs und private feste Weiterleitung; tatsächliche persistente Jobs, Idempotenz und Polling. Finale Transport-/Security-/echte PG-APIabnahme: 40 bestanden (25.87s). Origin/Sitzung vor Seiteneffekten, unbekannte/duplizierte/zu große Eingaben, Decimal-/Zeitgrenzen, Idempotenz409, signierte fremde/manipulierte Cursor, Wiederladen und persistente Cursorsignatur nach Dienstneustart geprüft. Jede öffentliche Antwort wird über whitelistbasierte DTOs kontrolliert. Listen selektieren nur Metadaten; Resultatpagination liegt mit höchstens limit+1 Zeilen in SQL. Blockiergründe und zulässige Datenreferenzen bleiben typisiert; SQL-/Providerdiagnosen verlassen den Dienst nicht. Die Python-Testdateien besitzen eindeutige Modulnamen, damit gemeinsame Tests ohne Importkollision laufen.

## BKT-08

Angular-Materialoberfläche mit generiertem Client, 1..12 chronologischen Folds, exakten Decimalgrenzen, dauerhaftem runId-URLbezug, Laufübersicht, Manifest, Foldkennzahlen/Referenzen, Equitykurve und Filltabelle. Polling nur aktiver Läufe und Abbruch bei Sitzungsverlust. Produktionsbuild bestanden; vorhandene Bundle-/Supervisor-CSSbudgetwarnungen bleiben Warnungen.

Vier echte Playwrightfälle mit kanonischer API, Worker und temporärem PostgreSQL final bestanden (14.3s): Formgrenzen ohne unerlaubte Writes, unabhängige Handrechnung 12000USD/20%, Reload mit unveränderlichem Manifest, echter BLOCKED-Lauf ohne Erfolgszahlen, tatsächlicher401 nach Cookieentfernung und Pollingstillstand. SQLnachprüfung bestätigt SUCCEEDED/BLOCKED sowie unveränderten Researchbarbestand und null Brokerorders. Browserfixture verwendet ausschließlich synthetische früh beobachtete Daten; kein persönlicher Providerzugriff.

## BKT-09 und übergreifende Abnahme

- `pytest tests/unit tests/property tests/contracts tests/security/test_backtest_access.py -q`: **2177 passed, 1 skipped**, 16.17s. Der bestehende Linux-tmpfs-Fall wird auf Windows übersprungen; tatsächliche Containerprüfungen sind separat ausgeführt. Vorhandene Starlette-Testclientdeprecation ist keine Testabweichung.
- `pytest tests/integration/test_research_storage.py tests/integration/test_backtest_acceptance.py tests/integration/test_backtest_safety.py tests/security/test_backtest_isolation.py -q`: **4 passed**, 68.40s. Bestehende Researchquery-/HTTPregression, vollständiger Worker mit Crash vor Publikation und gefencter Übernahme, identischer gebundener Manifest-/Ergebnishash beim Replay, zweiter unabhängiger Lauf mit identischem fachlichem Ergebnishash; neue Auftragsannahme bindet ausdrücklich einen eigenen Snapshottimestamp und damit ein eigenes Manifest. Bekannte Handrechnung 12000USD/20%, spätere blockierte Folds behalten fertige Foldreferenzen, heutiger Backfill bleibt BLOCKED; vorhandene nichtleere Finanzledger unverändert, null Backtest-HTTPaufrufe.
- `pytest tests/integration/test_backtest_jobs.py tests/security/test_backtest_roles.py -q`: **2 passed**, 48.54s; endgültige Migration einschließlich unveränderlicher Strategierevisionen, Queue/Fences/Recovery und reale Rechte.
- `pytest tests/contracts/test_backtest_transport.py tests/security/test_backtest_access.py tests/integration/test_backtest_api.py -q`: **40 passed**, 25.87s; endgültige API-/DTO-/Cursorabnahme.
- `scripts/test-backtest-browser.ps1 -SkipBuild`: **4 passed**, 14.3s plus echte SQLnachprüfung. `scripts/test-demo-browser.ps1 -SkipBuild`: **1 passed**, 17.6s plus SQL. `scripts/test-paper-browser.ps1 -SkipBuild`: **1 passed**, 27.7s plus SQL für synthetischen LIMIT/FILL/SELL/Cancel-/Recoverypfad. Keine echten Provider-/Brokerrequests.
- `scripts/validate-contracts.py`: OAS3.1.2, 58 Operationen, 27 Schemas und Beispiele bestanden; kanonischer Vertrag seit BKT-01 unverändert. Tatsächliches `oasdiff` und bytegleiche Doppelgenerierung/TypeScriptprüfung siehe BKT-01.
- `scripts/openspec.ps1 validate --all --strict`: **16 passed, 0 failed**. Ruff für alle neuen/berührten Backtestmodule bestanden; Secret-Patternscan **964 Quelldateien, 0 Befunde**.

### Unabhängiger Verify- und Safetyreview

Unabhängige Subagenten prüften Verträge, Datensichten sowie abschließend Vollständigkeit, Korrektheit und Kohärenz gegen acht Requirements und 15 Szenarien. Befunde zu Vollkörperreads in Listen, Decimal-/UTCabweichungen, Providerlabel, erstem tatsächlichem Kalenderzeitpunkt und Codeversionsbindung wurden behoben und gezielt nachgeprüft. Der abschließende Codeversions-Nachreview meldet keine offenen Findings. Preisrundungsreste und konfigurierte Slippage sind explizit getrennt dokumentiert.

| Requirement | Szenarien | Konkrete Evidenz |
|---|---|---|
| REQ-BKT-001 | T-BKT-001/002 | Modell-/Strategie-/Request-/Versionstests; vollständige Crash-/Replayabnahme |
| REQ-BKT-002 | T-BKT-003/004 | 14 Dataset-Unitfälle, echter PG-Snapshot/Keyset/Oracle/Kalender/Cutoff, heutiger Backfill |
| REQ-BKT-003 | T-BKT-005/006 | unabhängige Simulatorrechnungen, Propertyfälle, Overnight/fehlendes Fillziel/Fensterende |
| REQ-BKT-004 | T-BKT-007/008 | Fold-/Manifest-/Requestvalidierung, deterministischer kalendergebundener Warm-up, getrennte Foldzustände |
| REQ-BKT-005 | T-BKT-009/010 | exakte Metriken, Referenzen, BLOCKED-Teilergebnisse, Browserdarstellung |
| REQ-BKT-006 | T-BKT-011/012 | PG-Idempotenz/Fence/Crash/Queue/Attempts, Byte-/Zeilenbudgets, aktive Lastisolation |
| REQ-BKT-007 | T-BKT-013/014 | echte Auth/Origin-/DTO-/Cursor-/Reload-/Browserabnahme, kompatible Contracts/Client |
| REQ-BKT-008 | T-BKT-015 | tatsächlicher gehärteter Container, SQLrollen/HBA, null HTTP und unveränderte Finanzledger |

Safetycheckliste: Broker-submit-Isolation, deterministische Risk-/Executiongates, LIVE unerreichbar, DISABLED/opt-in-Default, Decimal/UTC, unabhängiges Kill/PAUSE und Reconciliation, Credential-/Mounttrennung sowie unveränderliche Referenzen/Audit **bestanden**. Bestehende Reservierungs-/Cancel-/Recoverygates sind unverändert und von der breiten Regression sowie synthetischer Paper-Browserabnahme erfasst. Der tatsächliche Backtestlasttest hält beim Simulatorcheckpoint keine SQLverbindung und bestätigt parallel PAUSE/KILL/Reconciliation unter fünf Sekunden. Der separate Container wurde mit nonroot, read-only, CapDrop ALL, 0.5 CPU/256MiB, zwei internen Testnetzen und HBA-Negativen geprüft; sein Worker kann weder Finanzledger lesen noch Coverage verändern oder als Admin aus dem Workernetz verbinden.

### Grenzen

Offlinezertifizierte synthetische Evidenz belegt Softwareverhalten, keine persönlichen Providerrechte oder historische Verfügbarkeit realer Daten. FIRST_OBSERVED und fehlende Kalender-/Kapitalmaßnahmenbelege können echte Läufe vollständig blockieren. Keine Profitabilitäts-/Total-Return-/LIVE-Freigabe; Dispatcher bleibt im Standard aus. Maximale Parameter garantieren keinen erfolgreichen Lauf innerhalb der harten Budgets. Quellen zur verwendeten APImechanik: offizielle SQLAlchemy- und HTTPX-Dokumentation als Fallback nach gemeldeter Context7-Monatsquote; keine privaten Daten in Dokumentationsabfragen.
