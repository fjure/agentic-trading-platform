# APW-10: Durchgehende Offlinezertifizierung und Betriebsabnahme

Stand21.10.2026: vorbereitende Teilevidenz. Task und Issue offen; die vollständige Handels-/Browser-/Recoveryabnahme ist noch nicht durchgeführt.

## Entwicklungsabhängigkeiten

Der normale Dashboard-Installationslauf meldete zwei npm-Schwachstellen: Vite8.3.0 enthielt ein verwundbares gebündeltes `launch-editor`. Alle drei zugehörigen Advisories nennen Vite8.3.3 als erste gepatchte Version dieser Linie. AngularBuild22.2.2 pinnt Vite exakt8.3.0; daher genügt eine bloße Lockdateiaktualisierung nicht.

Ein auf `@angular/build@22.2.2` begrenzter npm-Override pinnt Vite8.3.3. Angular/Material22.2.2 bleiben erhalten. Die Lockdatei enthält die erforderlichen kompatiblen Rolldown-Abhängigkeiten. Keine erzwungene Aktualisierung, kein Angular-Downgrade, kein manuell gepatchtes Paket.

Mit projektbezogenem Node22.23.3/npm: saubere `npm ci`-Installation, Produktionsbuild, Lockdatei-Audit und Audit des tatsächlich installierten Baums bestanden. Beide Audits melden0Schwachstellen. Installierte Versionen: Vite8.3.3 und gebündeltes `launch-editor`2.14.2. Bestehende Größenwarnungen bleiben sichtbar: initiales Bundle744,86kB bei500kB-Warngrenze, Supervisor-CSS7,12kB bei4kB-Warngrenze; Fehlergrenzen bestanden.

Die Schwachstellen betreffen den Editorstart des Entwicklungsservers, insbesondere Windows/WSL. Das normale Deployment liefert statische Produktionsdateien aus. Der Patch ist trotzdem Bestandteil der Entwicklungsabnahme.

Quellen: [Vite8.3.3](https://github.com/vitejs/vite/releases/tag/v8.3.3), [High-Severity-Advisory](https://github.com/advisories/GHSA-587h-7wmj-h5pm), [Editor-Argumente](https://github.com/advisories/GHSA-r825-w8mm-5pxw), [Windows-Escaping](https://github.com/advisories/GHSA-698v-4r3x-3vcv), [npm scoped overrides](https://github.com/npm/cli/blob/latest/docs/lib/content/configuring-npm/package-json.md).

## Grenzen

Diese Teilevidenz schließt APW-10 nicht ab. Insbesondere fehlen noch der vollständige Paperlebenszyklus, Order-/Fill-/Cancel-/Recoverykette, Browserabnahme und abschließende Git-/CI-Integration. Keine tatsächliche Alpaca-Fähigkeit wird durch lokale Doubles oder npm-Prüfungen bestätigt.


## Sicherheits-Patchstand24.10.2026

Die frühere Nullbefund-Aussage beschreibt den21.10.2026. Der spätere Audit fand3Befunde (AngularCLI high, Zod high, fast-uri moderate). Offizielle npm-Metadaten bestätigen CLI22.2.4 mit Zod4.6.6; Framework-/Material22.2.4 ist nicht durchgängig veröffentlicht und wurde deshalb nicht pauschal angefordert. Nur CLI wurde exakt auf22.2.4 angehoben und fast-uri innerhalb seiner bestehenden Semvergrenze auf den gepatchten Stand aktualisiert. Der vorhandene enge Vite8.3.3-Override und Angular/Material22.2.2 bleiben erhalten.

Sauberes `npm ci --ignore-scripts` und anschließender JSONaudit melden0Schwachstellen. Produktionsbuild bestanden3,859s mit identischem Bundlemain-UGVKQMV2/831,20kB; bestehende Bundle-/Supervisor-CSSwarnungen bleiben erhalten. Zusätzlich wurden alle242installierten Einträge über Version, Integrität und Herkunft mit der Projektlockdatei sowie die tatsächlichen Paketversionen abgeglichen:0Abweichungen. Keine erzwungene Dependency-Autoreparatur oder globale Konfiguration.

APW-09 ist regulär auf main698219b integriert; CI40130809310 erfolgreich. Die Uhrkompatibilität und der neue echte Browserharness werden getrennt in `apw-10-clock.md` und `apw-10-verification.md` geführt. APW-10 bleibt offen.

## Tatsächliche positive Browserkette25.10.2026

`./scripts/test-paper-browser.ps1 -SkipBuild`: **1 bestanden**, Browser23,1s,
Gesamtlauf24,2s; anschließend eigener SQL-Abnahmemarker erfolgreich. Eigene
temporäre PostgreSQL-Instanz, gemeinsame laufende Testuhr, unveränderte
IANA-New-York-Sitzungen, private Setup-/Paperdienste, öffentliche API und gebautes
Dashboard. Paper startet ohne Generation, Konto, Profil, Freigabe oder Order.
Alle fachlichen Schreibaktionen erfolgen über die tatsächliche Oberfläche;
nur Providerantworten und explizite Fills/Cancelterminal sind synthetisch.

Die Kette prüft Speichern/Leerung privater Formulare, VERIFY, PREPARE, Profil mit
Budget100, START, BUY1@15 mit Fill, SELL1@14,99 mit Fill, weiteren BUY und
Cancel204/Lookup/Terminalklärung, Reload, Trace sowie PAUSE/STOP/KILL. Abschließend
3unverwechselbare menschliche Orders,2Fills,1DELETE, Cash99999,99, keine offene
Reservation, HALTED und unveränderte9Fake-Finanztabellen/Demo-Runs. Vor jedem
Provider-I/O bestätigt ein threadbezogener tatsächlicher Engine-Observer keine
offene SQL-Transaktion des aufrufenden Executionthreads; vor POST/DELETE werden
die bereits dauerhaft gespeicherten Versuchsaudits gelesen. Unabhängiger Worker
und Watchdog laufen gleichzeitig; unerwartete Hintergrundfehler sperren PASS.

Chromiums CDP kann beim von Angular konsumierten no-store-Fetchstream den Body
verlieren. Ein ausschließlich testseitiger Beobachter liest deshalb eine Kopie
der echten ausgewählten Schreibantwort im Browser. Er verändert weder Fetch-
Argumente noch Antworten, erfasst keine ausgehenden Formulardaten und persistiert
nichts. Status-/Redaktionsprüfungen bleiben verbindlich. Primärquelle:
[Playwright-Issue42742](https://github.com/microsoft/playwright/issues/42742).

Vollständige Core-Regression: **1713bestanden,1bestehender Linux-Skip,
1bestehende Starlette-Warnung,13,09s**. Provider:13gezielte Tests bestanden;
Ruff sauber. Unabhängiger begrenzter Harnessreview ohne offeneP1/P2. Diese
positive Kette ersetzt noch keinen tatsächlichen Prozesscrash, die übrige
Safetymatrix, Deploymentabnahme oder finale Git-/CI-Integration. APW-10 bleibt offen.

## Weitere tatsächliche Abnahme25.10.2026

Der endgültige Harness führt Execution als eigenen OS-Prozess aus. API,
Einrichtungsdienst, Worker, Watchdog und synthetischer Provider bleiben bei
seinem Tod bestehen. Alle fünf Szenarien beginnen mit eigener leerer Paper-DB;
die Produktionsklassen, Rollen, Kalender und Gates bleiben unverändert.

| Szenario / Aufruf nach geprüftem Build | Ergebnis und tatsächlicher Nachweis |
|---|---|
| Ohne Schalter | 27,3s Browser /28,6s Gesamtaufruf; vollständige menschliche BUY-/SELL-/Fill-/Cancel-/Kontrollkette und SQL-Abschluss |
| `-Crash` | Finale Wiederholung55,4s/56,5s: SIGKILL nach gespeichertem DELETE-Versuch, verlorene Antwort, neuer Prozess, höhere SQL-Fence, Lookup statt zweitem DELETE; weiterhin3POSTs/2Fills/1DELETE/Cash99999,99/HALTED |
| `-Model` | 11,1s/12,2s: tatsächlicher Workerjob und DecisionResult erzeugen MODEL_DECISION-LIMIT ohne HumanRequest, Risk/Reservation/Submit und Fill; genau1POST/1Fill/0DELETE, Trace und KILL→HALTED |
| `-DayBoundary` | 17,2s/18,3s: tatsächliches PREPARE vor UTC-Mitternacht; danach exakt eine unverändert abgelaufene FIRST_DAY-Referenz, echter `read_daily`-/Riskbeleg `PAPER_DAILY_EVIDENCE_UNPROVEN`,0Orders |
| `-SubmitCrash` | 1,6min Browser/1,7min Gesamtaufruf: SIGKILL nach SUBMITTING-Marker, Broker nimmt ursprünglichen POST an; Reload zeigt dieselben IDs/Terms, neuer Owner/Fence, Wiederaufnahme nach echter90s-Kommando-Lease trotz ursprünglicher TTL; genau1POST/1Fill/0DELETE/Cash99985/HALTED |

Beide Crashfälle prüfen den tatsächlichen Prozess-Exitcode−9 und eine höhere
SQL-Owner-Fence. Keine SQLzeit wird verändert, kein Fristablauf übersprungen,
kein Ledger admittiert und keine Reservation bei unklarem Ergebnis freigegeben.
Der Submitfall beweist ausdrücklich erhaltene Originalterms und Lookup statt
erneutem POST. Sämtliche Modi prüfen die gemeinsamen unveränderten Fake-/Demo-
Fingerprints, keine externen Requests und keine unerwarteten Hintergrundfehler.

Ergänzende tatsächliche Cancel-/Readmatrix: **10bestanden139,75s**, einschließlich
echter Lease-/Crashrecovery, dauerhaftem429-Backoff, Teilfüllung und Vollfillrace.
Deployment-/Rollen-/Execution-/Secretgrenzen: **35bestanden161,22s**, keine Skips
bei `PAPER_DOCKER=1` und `PTV21_DOCKER=1`. Gesamte bestehende Fixture-Browsermatrix:
**30bestanden42,5s**,4ausgewiesene separate Owner-Skips; die drei neuen Paper-
Ownerfälle liefen wie oben tatsächlich. Die separate Demo-Ownerabnahme ist in
APW-09 mit13,7s dokumentiert.

Aktueller npm-Audit: alle Schweregrade0. Kanonischer Vertrag:46Operationen,
25Schemas,24primäre/28Antwort-/4Problembeispiele gültig.137Clientdateien in zwei
bytegleichen frischen Generierungen, vollständiger Anwendungsdriftvergleich und
TypeScriptfixtures bestanden. Oasdiff1.33 gegen tatsächliches main698219b meldet
keine Änderung; SHA256 `f1dc3a9c1c1393ea96c118eae6a9f52172e5edc5ea1d5b26c9c9e85742c1a7f5`.
Striktes OpenSpec und Ruff der neuen Quellen bestanden.

Normale bestehende2Fake-/Demo-Datenbanken: jeweils9Finanztabellen unverändert.
Alle19freigegebenen Runtimequellen stimmen nach kanonischer LF-Normalisierung
mit dem Workspace überein. Die einzige rohe Zeilenendenabweichung betrifft
`services/runtime/provision_paper.py` nach dem Gitcheckout; kein Inhaltsdrift.
Angewandte Migrationen19/20 wurden nicht verändert.

Der breite historische SQLlauf am Sonntag zeigte korrekt abgewiesene
Wochenend-Fixtures. Ein enges Opt-in `PAPER_INTEGRATION_TEST_CLOCK=1` setzt im
bestehenden Wegwerf-Paperhost dieselbe bereits zertifizierte laufende Uhr für
PostgreSQL und Python ein. Defaultverhalten und alle fachlichen Gates bleiben
erhalten; die historischen synthetischen Venue-Fixtures werden nicht als neuer
echter IANA-Kalendernachweis ausgegeben. Vier tatsächliche Lifecycleprüfungen
bestanden damit37,22s, die weiteren15Paper-SQLfälle189,08s.

Der ursprüngliche64-Fälle-Lauf endete mit53bestanden/11kalenderbedingten
Fixturefehlern in1112,94s. Sämtliche11Fehler gehörten zu den19anschließend mit
gemeinsamer Uhr bestandenen Paperfällen; die45übrigen Broker-/Replay-/Smoke-
Fälle waren erfolgreich. Überlappende Teilmengen werden nicht addiert.

Replay-/Recoverylauf:44bestanden/1Fehler in722,24s. Ein alter Testtrigger für
`pause-gap` wartete auf `SELECT current_user`, obwohl die produktive
Identitätsprüfung seit APW-02 Datenbank/Rolle/Login gemeinsam liest. Dadurch
wurde die beabsichtigte Pause nie ausgeführt. Der Test beobachtet jetzt den
tatsächlichen Engine-Transaktionsbeginn, liest separat committed SUBMITTING und
führt dieselbe auditierte Pause vor dem zweiten Gate aus. Alle Assertions
bleiben erhalten: SubmitDenied,0Submit-/Capability-I/O, REJECTED. Gezielte
Wiederholung:1bestanden24,00s,10nicht ausgewählte Fälle. Unabhängiger Review
bestätigt die echte Transaktionslücke ohne Gateoverride oder Produktivänderung.
Damit sind alle45Replay-/Recoveryfälle belegt;44unveränderte bestandene Fälle
wurden nicht erneut ausgeführt.

Lokale Verifikation, Safetyreview und Sourcescan711/0 sind abgeschlossen;
keine verbleibenden konkreten P1/P2. Alle10Tasks sind lokal umgesetzt.
Issue41 ist geschlossen; vollständiger10-Issue-Read-back bestätigt. Idempotente
Wiederholung:0erstellt/0aktualisiert/10unverändert. Die Git-/CI-Integration wird
im folgenden Abschluss nachgeführt. Der tatsächliche Remote-main wurde zuvor
erneut als698219b eingelesen; keine ungeprüfte Zwischenänderung.
