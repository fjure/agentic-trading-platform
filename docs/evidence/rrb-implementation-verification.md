# Retrospective Research Backtesting: integrierte Offlineabnahme

Apply-Auftrag vom 27.10.2026; acht Issuepakete RRB-01 bis RRB-08. Diese Datei sammelt die tatsächlich ausgeführte technische Abnahme. Synthetische Quellen, Authorities und lokale HTTPdoubles bestätigen keine persönlichen Providerrechte, reale historische Verfügbarkeit oder Profitabilität. Produktionstrust, Standarddispatch, Paper und LIVE werden nicht aktiviert. Keine Archivierung oder Main-Spec-Konsolidierung.

## Implementierung und nachvollziehbare Szenarien

Die Oberfläche und API unterscheiden Strict PIT und ausdrücklich retrospektive Exploration. Dieselben heute ingestierten historischen Bars bleiben Strict BLOCKED und dürfen nur nach eigener gebundener Vorschau retrospektiv ausgewertet werden. Ein gemeinsamer Decimal-Simulator erhält getrennte geschlossene Validierungskontexte. Tatsächliche Aufnahmezeiten werden unverändert gespeichert; retrospektive Fills nennen eine simulierte Marktgrenze. Berichte trennen historische Verfügbarkeit, Integrität, Kalender, Maßnahmen, Feed und Lizenz. UNKNOWN ist kein Maßnahmenfreiheitsnachweis und keine Rechtefreigabe.

| Szenarien | Ausgeführte fachliche Evidenz |
|---|---|
| T-RRB-001/002 | RRB-06 echte PostgreSQL-/öffentliche Relay-/private API-/Workerkette; RRB-07 echte Browserfälle mit gleichem Backfill, Strict-Default und ohne Fallback |
| T-RRB-003/004 | RRB-02 Snapshot-/Revision-/Budgetunitmatrix; RRB-03 Parallelcommit, Retention und Restore; RRB-06 aktuelle Korrekturen aller vier gebundenen Quellen und unveränderte Wiederaufnahme |
| T-RRB-005/006 | RRB-04 unabhängige Decimal-Handrechnungen, Zukunftsperturbation und nächste zulässige Fillkerze; gemeinsamer Simulatorkern |
| T-RRB-007/008 | RRB-02/04 Literal-Strict-Hashorakel und wiederholte gleiche Manifeste; RRB-06 echter Neustart mit Fences und eingefrorenen Referenzen |
| T-RRB-009 | RRB-03/06 echte SQL-Crash-/Rollback-/Altworker-/Queueprüfungen; RRB-07 Vorschau ohne Laufanlage |
| T-RRB-010/011 | RRB-01 kanonische geschlossene Verträge und generierter Client; RRB-06 tatsächlicher Routenabgleich, Auth/Origin/Cursor; RRB-07 Reload und sechs Kontexte |
| T-RRB-012 | RRB-08 zusätzliche tatsächliche Last-/Rollen-/Containermatrix; Ergebnisse unten |
| T-RRB-013/014/015 | RRB-05 reine Reliabilitymatrix mit unbekannten Nennern, Prüfbudget, fehlenden Bars und belegten Sitzungen; RRB-06 persistierte Reportseiten |
| T-RRB-016/017/018 | RRB-05 bekannte strukturelle Maßnahmen, Konflikte, Dividenden und beidseitige 20-Prozentgrenzen einschließlich Sitzungswechsel; RRB-06 spätere Maßnahmensperren; RRB-07 tatsächlicher Split blockiert auch flat |
| T-RRB-019/020 | RRB-06 aktuelle Lizenz-/Trustgates gegen direkte SQL-Publikation; RRB-07 erzwungen frischer Prüfstand vor POST, Formularinvalidierung, gebundene Pagination und Sessionverlust |

Ausführliche Kommandos, Ergebnisse und Grenzen der getrennten Lieferungen stehen in rrb-01.md bis rrb-07.md. Published Migrationen 0033 und 0034 bleiben unverändert.

## Reparaturen aus der unabhängigen Gesamtabnahme

Die anfängliche Workerabfrage selektierte Bars und Qualitybeobachtungen lediglich nach Instrument und Zeitraum. Dadurch konnte eine unabhängige Tagesreihe die feste 15-Minuten-Reihe blockieren. Die Auswahl bindet jetzt vor dem begrenzten SQLabruf alpaca/iex/15Min/raw sowie Qualityintervall 15Min. Evidenzperioden bleiben fachlich unverändert. Die reine Assessorprüfung weist tatsächlich gemischte Eingaben weiterhin zurück.

Eine pauschale Grenze von 10000 wiederholten Manifestreferenzen blockierte gültige SMA-Auswertungen unter dem vereinbarten Bytebudget. Sie ist durch inkrementelles kanonisches Bytezählen vor Aufnahme jedes ausgegebenen Fragments ersetzt; die abschließende exakte Grenze bleibt 12 MiB. Eine reale SMA100-Fixture mit 208 eindeutigen Bars und 10900 ausgegebenen Referenzen passt; eine größere Fixture scheitert begrenzt. 61 fokussierte Worker-/Reliabilitytests bestanden, 17.18s; Ruff und Formatprüfung bestanden.

Der exakte Paper-Provisionierungsprüfer erkennt jetzt auch die beiden additiven Migrationsstände. Die bisherigen 299 ACLerwartungen für 0032 bleiben unverändert; 0033 ergänzt acht und 0034 vier eng begrenzte Erwartungen für bereits in den Migrationen definierte Control-API-Lese-/Request-/Gateports. Diese Reparatur erteilt keine Grants und ändert weder Paper-Worker-/Executionrechte noch Migrationen. Unbekannte oder mehrfache Heads, zusätzliche/fehlende Rechte, Grantoptionen, Loginrechte, Objektbesitz und CREATE/TEMP-Drift bleiben geschlossen abgewiesen. 36 fokussierte Tests bestanden (2.57s), unabhängig erneut 36 passed (1.79s); Quellreview ohne P1/P2.

## Tatsächliche integrierte Laufzeitabnahme

Die drei neuen Tests verwenden echte PostgreSQLrollen, öffentliche authentifizierte HTTPoperationen, tatsächliche Assessor-/Simulatorarbeit und einen gesonderten gehärteten Workercontainer. Vor der Reparatur bestanden PostgreSQL-Gesamtkette und Container, während die Paperlastprüfung korrekt vor der Einrichtung mit PAPER_PRIVILEGE_MANIFEST_UNSUPPORTED stoppte. Nach Ergänzung des exakten Manifests bestand die vollständige unveränderte Abnahmematrix: **3 passed, 90.79s**.

Kommando: PAPER_INTEGRATION_TEST_CLOCK=1; .venv/Scripts/python.exe -m pytest tests/integration/test_retrospective_acceptance.py tests/security/test_retrospective_isolation.py -q --tb=short. Alle Tests liefen seriell außerhalb der lokalen Windows-Socketbeschränkung.

- Die PostgreSQL-/HTTPkette verwendet dieselben heute aufgenommenen historischen Bars: Strict BLOCKED, Vorschau ohne Simulator und retrospektiv ausschließlich EXPLORATORY_WITH_LIMITATIONS mit UNKNOWN/NOT_PROVEN. Eine unabhängige Tagesbar und eine INVALID-Tagesqualitätsbeobachtung werden nicht gebunden. Nach echter späterer Kurskorrektur bleiben alter Report, Snapshot und Ergebnis unverändert; der unabhängig erwartete Endwert beträgt 1200 USD. Eine spätere vertrauenswürdige synthetische Splitmeldung blockiert neue CPUausführung und Ergebnisanlage. Vollständige Zeilenfingerprints einer nichtleeren Finanzbasis bleiben identisch.
- Die Paperlastkette besitzt bereits einen synthetischen Bestand von 60 Anteilen. Eine retrospektive Simulation, ein Strict-Lauf, eine Readinessdiagnose und 97 Assessments füllen den gemeinsamen 100er-Jobpool; der 101. Auftrag scheitert geschlossen. Während echter Simulatorarbeit bleibt kein SQLpoolslot ausgecheckt. Parallele PAUSE-/KILL-Aufträge und Reconciliation schließen zusammen in weniger als fünf Sekunden ab, während die CPUarbeit noch aktiv ist. Anschließend sind Zustand und Risklatch HALTED, das Entrygate geschlossen und die vollständigen stabilen Finanz-/Order-/Risiko-/Kostenbasisfingerprints unverändert. Nur die ausdrücklich ausgeführten Safetyaktionen dürfen Status-/Audit-/Reconciliationspuren ergänzen.
- Der getrennte Workercontainer verarbeitet beide Modi und eine Vorschau tatsächlich als UID10001, read-only, höchstens 0.5 CPU/256 MiB, ohne private Mounts oder Credentials. Alle zehn überwachten HTTP-/Broker-/Provider-/Jev-Fähigkeitszähler bleiben null. Finanzzugriff und unerlaubte Writes scheitern mit tatsächlichem SQLSTATE 42501; eine Adminidentität ist aus dem Workernetz per HBA unerreichbar. Die Kontrolle misst die ausgeübte CPU-/100-Job-Fixture und behauptet keine Latenzgarantie für jede denkbare 100000-Zeilen-Konfiguration.

Der gemeinsame isolierte Testclock wird auf der aktuellen Produktionsbasis gebaut, damit die schon produktiv gepinnte jsonschema-Abhängigkeit und das kanonische Schema vorhanden sind. Bibliotheksquelle und beide Bibliothekshashes bleiben unverändert; Clockvariablen bleiben aus den Imagevorgaben ausgeschlossen. Die tatsächliche Python-/PostgreSQL-Kompatibilitätsprüfung bestand mit 2 passed, 8.00s. Eine ältere lokale Digestreferenz wurde nach dem reproduzierbaren Neubau auf den tatsächlich gebauten Digest aktualisiert.

Nach der Provisionierungsreparatur wurde das Produktionsimage erneut mit uv sync --frozen --no-dev gebaut: c40719cab30222c1dd7032d68efd6b7206834ccfd3cdc3c06034b607248f7f14. Der Smoke als UID10001, read-only, ohne Netzwerk, Mounts, Credentials oder zusätzliche Capabilities bestätigt Quellfingerprint, Schema, 83 DTOs, drei Relaymethoden, deaktivierten Worker ohne SQLzugriff und das exakte 311er-ACLmanifest. Finale Testclockimages: Python aa02183f67a30b2c9757c09486a98d831dea59fbc5886eaea87b25d6179cd84b; PostgreSQL d1aca1848ba6d3695791d5d3a92662b3547a10eab754bdd7344d87e13a1ca4f0. Ihre echte Prozesskompatibilität bestand erneut: 2 passed, 6.65s.

Die finale zweifache Angularclientgenerierung erzeugt 285 bytegleiche Dateien; der vorhandene Anwendungsklient stimmt vollständig überein. TypeScript einschließlich Decision-/Paper-/Researchfixtures bestanden. oasdiff 1.33.0 gegen main 8c08277b4f90a1f3d74c204f511a107efdf85e0f: null Breaking-Errors, eine bekannte strukturelle Warnung zum unveränderten Readiness-allOf. Kanonischer OpenAPI-SHA256 unverändert: 72a21c9661c7760775375df8282756b0187422ba0e42b7973b79c76934865923.

Breite finale Unit-/Property-/Zugriffsschutzregression: **1378 passed, 1 skipped, 14.05s**. Sie umfasst sämtliche Unit- und Propertytests sowie Backtestzugang, Researchprovider/-relay/-trust, Paperlaufzeitidentität und die statischen LIVE-/Submit-Isolationsgrenzen. Der einzige Skip ist der bestehende Linux-tmpfs-Fall auf Windows; echte Containerprüfungen wurden separat ausgeführt. Bestehende Starlette/httpx-Abkündigungswarnung. Testdateien und genaue Kommandos stehen in den verknüpften Quellen und den oben genannten Testpfaden; keine gelockerten Orakel.

Die finale Bestandsmatrix aus Backtestjobs/-dataset/-API/-acceptance, Readinessjobs-Recovery/-storage/-API/-acceptance, Backtest-/Readinessrollen sowie Researchstorage/-collection/-evidence-import bestand seriell: **14 passed, 478.30s**. Sie verwendet echte lokale PostgreSQL-Datenbanken, HTTPpfade, Fences, Rollen und Restore; die Paperfixture verwendet ausschließlich den gesonderten verifizierten Testclock. Keine produktiven Datenbanken oder Providerzugriffe.

Die abschließende zusätzliche PostgreSQL-/Safetymatrix bestand: **43 passed, 1076.85s**. Geprüft wurden retrospektive API, Storage, Rollen und Crashwiederaufnahme sowie bestehende Backtest-/Researchlastisolation, öffentliche Paperkette, alle sechs Cancelketten, hundert parallele Risikokonkurrenten, elf Submit-/Crashszenarien und achtzehn Reconciliationprüfungen. Sie verwenden ausschließlich isolierte Testdatenbanken, lokale Providerdoubles und FakeBroker. Hier simulierte Fakeaktionen sind keine externen Brokerorders; Forschungsfälle besitzen weiterhin null Fähigkeitsaufrufe.

Finaler vollständiger Vertrags-/Transport-/Routenlauf: **1899 passed, 27.59s**, seriell ohne weitere Last. Alle 66 tatsächlichen öffentlichen Operationen werden ohne Stufenausnahmen geprüft. Bekannte Starlette/httpx-Abkündigungswarnung. Ruff auf allen sieben geänderten/neuen Pythonpfaden bestanden; die fünf neu formatierten Worker-/Testdateien bestehen auch die Formatprüfung. Quellscan: 1260 Dateien, null Credentialmusterbefunde; strenge OpenSpec-Validierung bestanden.

Nach sämtlichen PostgreSQLtests wurde ausschließlich der PostgreSQLdienst mit der Basiskonfiguration wiederhergestellt. Der ursprüngliche Volume agentic-trading-lab-dev_postgres_data war erhalten und ist wieder eingebunden; init=true, Healthstatus healthy, pg_isready akzeptiert Verbindungen. Kein Volume wurde gelöscht, keine ursprüngliche Datenbank migriert oder repariert und kein Handelsdienst gestartet. Die Testoverride und kurzlebigen Testdaten wurden nicht in Git aufgenommen.

## Safetyreview und Grenzen

Der unabhängige Quellreview meldet nach Reparatur der Auswahl-, Bytebudget- und Provisionierungsbefunde keine verbleibenden P1/P2. Die tatsächliche Runtimeevidenz und Bestandsregression bestätigen folgende Grenzen:

| Invariante | Konkrete Evidenz | Status |
|---|---|---|
| Research kann keinen Broker-submit auslösen | Tatsächlicher eingeschränkter Workercontainer, zehn Fähigkeitszähler null, SQLSTATE42501 und HBA-Negative | PASS |
| Deterministische Risk-/Executiongates; LIVE unerreichbar | Vollständige Unit-/Propertysuite, statische Modus-/Importgrenzen und echte parallele Risikoreservierungen | PASS |
| Deaktivierter Standarddispatch, fail-closed und sicherer Restart | Netzloser Produktionssmoke mit DISABLED ohne SQL; elf Submit-/Crashfälle und retrospektive Fence-/Restartmatrix | PASS |
| Decimal und UTC | Unabhängige Simulationshandrechnungen, Literal-Strict-Hashes, Propertytests und unveränderte Ingestionzeiten | PASS |
| Unabhängiges Kill/PAUSE_NEW/Reconciliation | Echte aktive Forschungs-CPU mit vollem 100er-Jobpool, kein SQLcheckout, beide Kontrollen/Reconciliation unter fünf Sekunden, HALTED und Entrygate geschlossen | PASS |
| Execution-only-Credentials und Redaktion | Kein privater Mount oder Providercredential im echten Workercontainer; Quellscan und öffentliche Transport-/Redaktionsnegative | PASS |
| Unveränderliche Proposal-/Riskreferenzen und Audit | Echte nichtleere Ledgerfingerprints, alte Fence-/Publikationsnegative, exakte Proposal-/Riskbindungen in Unit- und Paperketten | PASS |
| Atomare Reservierung unter Beständen, offenen Orders und Reservierungen | Hundert tatsächliche PostgreSQL-Konkurrenten, Budget-/Cashproperties und Submit-Rechecktests | PASS |
| Ablaufprüfung vor Submit, unbekannter Status hält Reservierung, Timeout vor erneuter Sendung reconciliieren | Elf Submit-/Crashfälle, echte Cancel-Timeout-/Restartkette und Reconciliationmatrix | PASS |
| Teilfüllungen und Cancel-/Fillrennen | Sechs tatsächliche Cancelketten, sechzehn wirtschaftliche Reconciliationszenarien plus Fingerprint-/Redaktionsfälle | PASS |
| Modellfehler blockieren keine Sicherheitskontrollen | Getrennte Runtimeports, Fail-closed-/Coordinatorunitfälle, echte Kontrollen unter Forschungs-CPU ohne Jevaufruf | PASS |

Es wird kein Return Score, Total Return, Echtwelt-Lizenznachweis oder Handelsfreigabestatus abgeleitet. Eine konservative Preisbruchregel kann echte Marktbewegungen blockieren und ersetzt kein Maßnahmenledger. Maximale Requestparameter garantieren keinen erfolgreichen Lauf innerhalb der harten Ressourcenbudgets. Alle produktiven Konsequenzen benötigen weiterhin einen eigenen Auftrag.
