# APW-07: menschliche LIMIT-Anfragen im gemeinsamen Riskpfad

Stand24.10.2026 (Europe/Berlin), Branch `codex/ptv-38-paper-manual-limit` ab geprüftem main d28d8d20c31413435cd11ead47f2e779a890d5e6. **Vollständige lokale Pflichtabnahme bestanden; Issue-/Gitintegration folgt.** Keine echten Schlüssel, Providerrequests oder Orders; sämtliche Brokerfälle sind lokale synthetische HTTPtransporte.

## Umsetzung

Ein bestätigter menschlicher USD-LIMIT-DAY-BUY/SELL bleibt als immutable HumanRequest mit Actor, ursprünglichem Annahmezeitpunkt, Generationen, Versionsbezügen, Menge, Preis und HTTPtrace erhalten. Der deterministische Adapter übernimmt Menge/Preis exakt oder weist ab. Keine künstliche Modellentscheidung entsteht. Die bestehende Proposal-Tabelle erhält einen exklusiven FK auf Modellentscheidung oder HumanRequest. Legacy-Dataclass/Eventpayload bleiben unverändert; ausschließlich tatsächliche menschliche Zeilen lädt der gemeinsame Ledgerport als strikt typisierte Humanprojektion.

Nur ein fester Execution-Port erzeugt Request, Proposal, Originalplanhash, Aktivierungslink und Audit atomar aus dem authentifizierten Kommando. Direkte Worker-Herkunftsfälschung bleibt trotz bestehender Modelleinschreibrechte verboten. Humantrace und Marktevidenztrace werden ehrlich getrennt referenziert. Gemeinsame Kontosperre, RiskDecision, Reservation, Intent und `submit_paper_once` gelten für beide Quellen; die zusätzliche Humanprüfung entfernt ausschließlich die eigene vorhandene Reservation einmal und erhält alle konkurrierenden Verpflichtungen samt Puffer.

Annahme ist kein Brokererfolg. Der bestehende öffentliche HTTPvertrag bleibt unverändert. Nur tatsächliche dauerhafte Ergebnisse erzeugen SUCCEEDED oder BLOCKED mit abgeleiteter Order-ID. Providerablehnung wird PROVIDER_REJECTED; Budget-/SELL-Races werden BUDGET_EXCEEDED bzw. INSUFFICIENT_POSITION. SUBMITTING/UNKNOWN bleiben RUNNING und reserviert. Vor neuer Verarbeitung wird dieselbe bereits vorhandene Verpflichtung geprüft; Reclaim verändert weder Terms noch TTL. Ein tatsächlicher späterer ACK kann auch nach Pause/Rotation/Ablauf abgeschlossen werden, ohne neue Freigabe oder POST. Marktfehler für neue Einstiege unterbrechen diesen belegten Abschluss nicht. Eine belegbar nie gesendete abgelaufene Reservation wird unter Accountlock auditiert freigegeben; mögliche Brokerwirkungen bleiben erhalten.

Migration17 speichert die neue Quelle. Die letzte NULL-Erfolgs-Härtung liegt ausschließlich in Folgemigration18, weil17 im lokalen inaktiven Lab bereits angewendet war. Keine Daten-/Volumenrücksetzung und keine manuelle DDLreparatur. Exakter Rechteumfang18 entspricht17; alte private Funktionen bleiben unzugänglich.

## Prüfevidenz

- Finale lokale Matrix1614bestanden,1bestehender Linuxskip,1Starlettewarnung/13,49s. Human- und bestehende Domain-/Risk-/Execution-/Contract-/Replay-/Propertyfälle; fokussierte Runtimefälle überschneiden diese Zahl.
- Tatsächliche Head18-Quelle-/Rollen-/Konkurrenzmatrix3bestanden/38,05s: strict HumanLoader, keine DecisionResult, unveränderte Terms/Trace, generischer vorgetäuschter Erfolg vor/nach Persistenz und falscher Owner verweigert, Worker-Fälschung abgewiesen, spätere Pause/Reclaim ohne TTLerneuerung oder Submit, sichere Unsent-Ablauffreigabe. Echte parallele sowie zusätzliche erzwungene Modell-zuerst-Budget-/SELLfälle mit genau einer Reservation und korrektem öffentlichen Ablehnungsgrund.
- Bestehende tatsächliche Submit-/Lifecycle-/LIMITmatrix gegen Head18:12bestanden/103,62s. Die genaue Revisionserwartung des alten LIMITtests wurde auf18 aktualisiert, keine Assertion entfernt.
- Tatsächliche Deployment-/Transport-/Secretgrenzmatrix18bestanden/102,69s. Normaler Launcher0018 vollständig gesund/inaktiv,12Quellcodedateien SHA-256-identisch zum laufenden Paperimage.0gespeicherte/aktive Paper-Schlüsselgenerationen und0Konten/Orders/Kommandos.2bestehende Fake-/Demo-Datenbanken über9Finanztabellen vor/nach17/18 unverändert.
- Unveränderter kanonischer Vertrag:41Operationen/20Schemas,19Schema-/23Responsebeispiele bestanden, oasdiff1.33.0 gegen tatsächliches main d28d8d2: No changes detected. Committeter Vertrags-SHA256af8009def1419a1f4e83fda29c6208a38098353ca425a1434830493c1d2ea420; generierter Client und HTTP-DTOs unverändert gegenüber grünem main-CI39934500495. Striktes OpenSpec, Ruff und Sourcescan634Dateien/0Treffer bestanden.
- Vollständige öffentliche API→PaperClient→privateAPI→SQL→Coordinator→Risk→Brokerdoublekette im echten18:45UTC-Frischefenster23.10. bestanden:1Test/748,54s einschließlich gebundener Wartezeit. START, sieben Negativfälle, Idempotenz/409, ehrliche Human-FK-/Actor-/Trace-/Versions-/TTLkette ohne DecisionResult, BUY-ACK→tatsächlicher FILL→öffentliche alte Einstiegssperre geschlossen→neuer expliziter START→SELL-ACK, Timeout-RUNNING→echte Reconciliation-ACK→SUCCEEDED ohne zweiten POST. Capabilitywechsel invalidiert belegbar Kontext und Freigabe; staleQuote stammt aus dem tatsächlichen Provideradapter. Projektion/Audit ohne Canary-/private KontoUUIDleaks.
- Separater unveränderter echter90Sekunden-Reclaimfall bestanden: öffentlicher ACK, simuliertes Prozessende vor Commandfinish, tatsächliche Pause und Pendingrotation, echte SQL-Leaseablaufzeit ohne Zeitstempeländerung, neuer Runtimeowner/Fence, identische Terms/TTL/Trace/Order-ID und SUCCEEDED ohne zusätzliche Credentialreads/ProviderHTTP/POST. Dieser Fall bestand im früheren Zwei-Fall-Lauf; dessen alter Hauptfall wurde durch den oben genannten endgültigen erfolgreichen Lauf ersetzt. Keine behauptete gemeinsame Zweifall-Passzahl.
- Unabhängige abschließende Source-/Safetyreviews einschließlich vorwärtsgerichteter18 und Pendingabschluss nach Marketfehler ohne offeneP1/P2. Alle für APW-07 verpflichtenden Invarianten tatsächlich abgedeckt; Cancel bleibt eigener APW-08-Umfang.

## Betriebsgrenzen

Die vorhandenen15Minutenkerzen-/120Sekunden-Frischegrenzen bleiben unverändert. Positive echte Laufzeitfälle beginnen im UTC-Viertelstundenfenster; außerhalb belegter Frische bleiben Einstiege geschlossen, während Recovery weiterläuft. Testdaten und Venuezeitzone sind ausdrücklich synthetisch, SQL-Uhr/Ready-/Risk-/Submitguards unverändert. Tatsächliche Alpaca-/Jevfähigkeit bleibt eine spätere private menschliche Abnahme. Cancel und volle Materialbedienung folgen in APW-08/09.

Gehaltene Dateien außerhalb dieses Pakets: Nutzer `apps/dashboard/angular.json`, APW-10 `apps/dashboard/package.json`, `package-lock.json`, `docs/evidence/apw-10.md`. TaskAPW-07 ist abgeschlossen, OpenSpec7/10; Issue38-Abgleich samt Read-back bestätigt, Wiederholung0Änderungen/10unverändert. Geprüfter Branchcommit16852af4855c45fb4e118ce78e8555c0184a128e regulär nach main78896a809b888c3d00b568e65235e18ea581d8fe integriert; Remotezuordnung und identischer Implementierungsbaum bestätigt. [CI40040855953 erfolgreich](https://github.com/fjure/agentic-trading-platform/actions/runs/40040855953).

## Safetycheckliste für diesen Umfang

| Grenze | Konkrete Evidenz | Ergebnis |
|---|---|---|
| Agent/API ohne Brokerport; Execution-only private Quelle | echter HTTPrelay, feste SQLrollen/Portverweigerung, Deploymenttests | PASS |
| Deterministische Risk-/Submitgates und atomare Konkurrenz | test_paper_human_sql.py, bestehende zwölf SQLregressionen | PASS |
| LIVE-Sperre, inaktive Defaults und kein Neustartstart | Config-/Contractnegatives, normaler0Generationen-Labstand, echter ACK-Reclaim nach Pause | PASS |
| Decimal/UTC; exakte bestätigte Terms | Humanpuretests, strikter SQLloader, echte BUY-/Fill-/SELLkette | PASS |
| Kill/Pause und finanzielle Sperre während Provider-I/O | bestehende tatsächliche Submitmatrix gegen18, Reclaim nach tatsächlicher Pause | PASS |
| Geheimnisse/Accountpin ohne öffentliche Leaks | HTTPcanaries, fixe Providerdoubles, Source-/Deploymentgrenztests | PASS |
| Immutable Quelle/Proposal/Risk/Intent/Trace | XOR/FK/Workerverweigerung und tatsächliche vollständige HTTPkette | PASS |
| TTL, UNKNOWN-Erhalt und kein Blindresubmit | Audit-before-I/O-Regressionsfälle, Timeoutlookup und echter90Sekunden-Reclaim | PASS |
