# Researchfoundation: Implementierungsnachweis

Stand26.10.2026: **9/9 Tasks abgeschlossen.** Implementierung und unten aufgeführte
Offlineprüfungen sind abgenommen; neun getrennte Issuecommits sind auf Remote-main
integriert. Issues42–50 sind geschlossen und vollständig zurückgelesen. Persönliche Alpaca-Datenrechte und ein echter berechtigter
Providerabruf bleiben **UNVERIFIED**. Keine echten Brokerorders wurden ausgelöst.

## Umfang und Architektur

Die Umsetzung verwendet PostgreSQL und additive Migrationen0021–0027. Historische
GETs laufen ausschließlich in bestehender Paper-Execution. Der öffentliche
Researchrelay nutzt die vorhandene private Paper-API. Research erhält eigene
SQLpools, eine eigene GETverbindung und begrenzte Workeraufnahme. Neue Handels-
oder Credentialberechtigungen entstehen nicht. LIVE bleibt technisch ausgeschlossen.

Implementiert sind unveränderliche SPY-/QQQ-Identitäten und Symbolgültigkeiten,
raw-IEX-Barrevisionen/Evidenz, konservative committed PITsnapshots, versionierte
Kalenderbelege, persistierte Qualitätsbefunde, dauerhafte Requests/Leases/Receipts,
begrenzte Retry-/Cursorwiederaufnahme, Quellenkatalog, Decimal-MarketContext,
sieben authentifizierte HTTPoperationen und die Materialoberfläche. Ein POST
unterscheidet Erstellung und ausdrückliche Fortsetzung; beide teilen denselben
Idempotenznamensraum. News/Fundamentals/Makro/Regime bleiben ohne echten Adapter
ehrlich nicht verfügbar. Bedienung und Grenzen: [Runbook](../runbooks/research-data.md).

## Tatsächlich ausgeführte Prüfungen

Überlappende Testmengen werden nicht addiert. Zahlen bezeichnen jeweils einen Lauf.

| Prüfung | Ergebnis |
| --- | --- |
| Abschließender Gesamtquellstand: Unit/Contract/Security + Research-PG/Safety + Paperprovisionierung |2135 bestanden,31 umgebungsabhängig übersprungen,7 Subtests;124,74s |
| Gesamte `tests/unit tests/contracts tests/security` |2115 bestanden,31 umgebungsabhängig übersprungen,7 Subtests;91,09s |
| Linux ohne Netzwerk: Papercredentials, Providersetup, Researchadapter/-relay |112 bestanden,14 Subtests;1,55s; auf Windows übersprungene POSIXprüfungen eingeschlossen |
| Echte PG-/HTTPkette `test_research_storage.py` |22,61s bestanden, einschließlich letzter Contextqualitätskorrektur |
| Echte Paper-Research-Poolauslastung `test_research_safety.py` |7,44s bestanden: tatsächliche Pause/Killannahme, Reconciliation und Heartbeat bei belegtem separatem Researchpool |
| Paper-PG-Provisionierung/Coordinator-/Cancel-/Readketten |10 bestanden;1 positive Realclock-Coordinatorkette wegen damaligem UTCwochenende übersprungen;131,77s Gesamtaufruf einschließlich eines anschließend behobenen Researchfixturefehlers |
| Tatsächlicher Researchbrowser mit aktuellem Produktionsbuild |2 Chromiumtests,10,3s: echte API/PG und lokales ProviderHTTPdouble, inklusive PARTIALfortsetzung/Replays/Reload/Sessionverlust |
| Bestehende synthetische UI-Gesamtregression |29 bestanden,7 kontextgebunden übersprungen,1 Locatorfehler durch zweite Research-Alertregion |
| Betroffene Paper-UI-Suite nach bereichsgebundener Locatorkorrektur |10/10 bestanden;19,6s; Vertrags-/Provenienznegativen unverändert geprüft |
| Letzte Context-/DTO-/Routenprüfung |40 bestanden;0,98s |
| Queryaudit-/Export-/Aufnahmegrenzen-/Relayprüfung |39 bestanden;0,95s |
| Angular-Produktionsbuild, Node22.23.3 |bestanden;Initialbundle860,42kB;bestehende Bundle-/Supervisor-CSSbudgetwarnungen, keine Budgetlockerung |
| Angularclient, Generator7.26.0 |167 Dateien zweimal bytegleich;generierter Bestand und TypeScript inkl. negativer typisierter Fixtures bestanden |
| Kanonische Verträge |OAS3.1.2,53 Operationen,26 Schema-Dateien;Schema-/Response-/Problembeispiele und bestehende Negativen bestanden |
| Echter oasdiff1.33.0 gegen main `c19c22ed107511b785901a242facbe5c9e33b762` |keine Breaking Changes |
| OpenSpec strikt nach Main-Spec-Sync |15/15 bestanden |
| Letzter Source-Secret-Scan |844 Dateien,0 Befunde;einschließlich Main-Specs und Integrationsnachweisen |

Sandbox-/Fixturefehler wurden als Fehler behandelt. Behoben wurden unter anderem
fehlende `date`-Kanonisierung, ein festgehaltener privater Exceptionkontext,
mehrdeutige NULLparameter beim vierten SQLretry und ein zu breit gewählter
Paper-Alertlocator. Die Fixturewarteschlange wird vor einem gezielten Resumejob
über die tatsächlichen gefencten Jobports abgearbeitet. Keine Handels-, Auth-,
Revisions- oder SQLrechte wurden zur Testumgehung gelockert.

## Requirement- und Szenarienzuordnung

Alle16Requirements und31Szenarien besitzen tatsächliche Prüfpfade. Die benannten
PGhelper werden vom echten `tests/integration/test_research_storage.py` aufgerufen;
sie ersetzen keine Datenbank durch ein Mock. Die Quelle allein wird lokal ersetzt.

| Requirement / Szenarien | Implementierung und Prüfevidenz |
| --- | --- |
| HMD-001 /001,002 |models/storage + Domain-/Symbolnegativen und tatsächliche PGconstraints |
| HMD-002 /003,004 |AlpacaHistorySource;Provider-Sicherheitstests und mehrseitige reale HTTP→Coordinator→PG→APIkette |
| HMD-003 /005,006,013,014 |jobs/ingestion;PGjobs-/resumehelper: Rollback, Fenceübernahme, dauerhafter Fortschritt, Fortsetzung und gemeinsamer Create-/Resume-Schlüsselnamensraum |
| HMD-004 /007,008 |Provider-/Jobs-/Limiter-/Ingestiontests:429,Retryzeit,vierter Retry→PARTIAL,Tokenzyklus/-ablehnung,Antwortgrößen,Budgets |
| HMD-005 /009,010 |calendar/quality;12 responsegebundene Kalenderprüfungen, DST/Earlyclose/2026grenzen, OHLCablehnung und persistierter PGqualitätsbefund |
| HMD-006 /011,012 |Execution-owned GET, SQLrollen/Canaries und unveränderte Ledger;Browser und Safetytest mit null erzeugten Orders |
| REV-001 /001,002 |kanonische Domain-/Quellinhalte, Fingerprints, Evidenz-ID;Qualität bleibt von Konfidenz getrennt |
| REV-002 /003,004 |PIT-/Contexttests sowie alte Wissensstände in tatsächlicher API;FIRST_OBSERVED,exakte Zeitgrenzen |
| REV-003 /005,006,009 |PGappend-only/Korrektur/A→B→A;alte Sicht stabil;Raw-/Adjustierungsnegativen |
| REV-004 /007,008 |snapshotgebundener Queryport, IDlookup und spätes Commit zwischen Seiten;gebundene Cursor und echte HTTPnegativen |
| RCX-001 /001,002 |Context derselben Sicht, zulässige Evidenzreferenzen und ehrliche fehlende Quellen |
| RCX-002 /003 |unabhängige Decimalberechnungen, mindestens3Bars und vollständiges erwartetes Fenster |
| RCX-003 /004 |getrennte Sourceports/Normalizer und Katalog;NOT_IMPLEMENTED liefert keine Dummydaten |
| RCX-004 /005,006 |kanonische Verträge, Auth/Origin/Zeit-/Größennegativen, dauerhafter202Job und Payloadkonflikt409 |
| RCX-005 /007 |echte Researchbrowserkette, leere/gesperrte/Retry-/PARTIALzustände,Reload,Fortsetzung,Sessionverlust |
| RCX-006 /008 |persistenter redigierter Jobaudit, begrenzter Queryaudit und Metrikexport,gemeinsamer Limiter,eigene Pools,tatsächlicher Paper-Safety-Lasttest |

## Unabhängiger Review

Getrennte Reviews identifizierten und überprüften Isolationsfehler beim
Researchstart, die geteilte API-SQLkapazität, fehlenden 429Backoff bestehender Leser,
falsche Vollbereichcoverage einzelner Seiten, über das Queryende hinaus erwartete
Bars sowie eine verfälschte Contextqualität. Diese Befunde sind behoben; die
betroffenen tatsächlichen API-/PG-/Browser-/Safetyprüfungen wurden erneut ausgeführt.

Der abschließende read-only Verify-/Safetyreview ordnet alle16Requirements und31
Szenarien den Tests zu und fand keinen weiteren konkreten fachlichen oder Safety-
Blocker. Seine zunächst bedingte Freigabe wurde nach Rückmeldung der bestandenen
letzten PG-/API-, Paper-Safety- und Paper-UIläufe geschlossen. Die absichtlich
fehlende persönliche Providerabnahme wurde nicht als erfolgreicher Abruf ausgegeben.

## Bestätigte Integration

Neun issuebezogene Commits und Branches42–50 wurden nach bestandener gemeinsamer
MDR-01/MDR-07-Abnahme geordnet integriert. Remote-main6276f79a49026189ea59eeb6b14d91a89a876f58
enthält sämtliche geprüften Implementierungscommits. Alle Branch-SHAs und ihre
Erreichbarkeit auf Remote-main sind verifiziert. Zuordnung:
`docs/development/github-research-git-integrations.json`. Wiederholter Issue-Sync:
0Änderungen,9vollständigeRead-backs. Drei Main-Specs wurden
synchronisiert und strikt validiert:11/11 Specs,15/15 einschließlich Changes. Die vorbestehenden fremden Änderungen
an README und Angular-Analytics bleiben außerhalb der Issuecommits. Keine PR oder
Archivierung ist Bestandteil dieses Nachweises.
