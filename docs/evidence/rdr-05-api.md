# RDR-05 – Dauerhafte, begrenzte Datenbereitschaftsdiagnose

Stand: 2026-10-27. Implementierter lokaler Diagnosepfad mit echten isolierten
PostgreSQLtests. Alle Marktinhalte und alten Beobachtungszeiten der positiven
Prüfung sind ausdrücklich synthetische Fixtures. Keine echte Provideranfrage,
keine Brokerorder, keine Handelsaktivierung und keine verifizierten Datenrechte.

## Laufzeit und Grenzen

`ReadinessService(engine, code_version=..., cursor_key=None)` bietet
`create_assessment(payload, actor, key, trace)`, `read_assessment(assessment_id)`
und `read_findings(assessment_id, values)`. `register_readiness_routes(...)`
bindet die drei kanonischen Operationen unter
`/api/v1/research/data-readiness/assessments` an. Gemeinsame App-/Relayverdrahtung
liegt in den vom Hauptagenten gepflegten Runtime- und APIdateien.

Sitzung, lokaler Akteur und Origin werden vor dem SQLport geprüft. Strikte
benannte DTOs verwerfen zusätzliche Felder und unzulässige Statuskombinationen.
JSON mit doppelten Schlüsseln oder nicht endlichen Zahlen wird abgelehnt.
Request und Response sind auf 16 MiB, parallele HTTParbeit auf zwei begrenzt.
Fehler verwenden den geschlossenen Problemvertrag mit Trace-ID; SQL,
Providerantworten und beliebige Exceptiontexte werden nicht transportiert.

Migration `0031_readiness_jobs` ergänzt die vorhandenen RDR-03-Tabellen um
versionierte Zulassung, Audit und unveränderliche Belegbindungen. Die
Security-Definer-Zulassung hat festen Suchpfad, entzieht PUBLIC-Rechte und
begrenzt atomar auf 32 wartende Diagnosen. Replay wird vor der Kapazitätsprüfung
behandelt: gleicher Akteur/Schlüssel/kanonischer Request liefert auch nach
Fertigstellung dieselbe ursprüngliche Annahme; abweichender Inhalt liefert 409.
Datensnapshot und Codeversion werden bei Annahme persistiert.

`ReadinessJobs.claim(owner)` liefert `ReadinessLease(assessment_id, owner,
fence, row)`. Diagnose- und Backtestqueue teilen einen SQL-Advisory-Lock und
einen Datenbanktrigger: höchstens ein nicht abgelaufener RUNNING-Job über beide
Queues, auch bei einem alten Backtestclaimer. Claims sind nach DBzeit geordnet;
Lease und CPUbudget betragen höchstens 300 Sekunden, die Rechenreserve greift
nach 295 Sekunden. Drei abgelaufene Versuche enden in FAILED/UNKNOWN mit
ATTEMPTS_EXHAUSTED. Es wird kein SQLlock während Assessorarbeit gehalten.

`BacktestWorker.tick()` verarbeitet Diagnosen im vorhandenen credentialfreien
Worker und verwendet ausschließlich den gemeinsamen RDR-02-Assessor. Kalender,
Qualität und Kapitalmaßnahmen werden unter einem wiederholbaren aktuellen
Trustsnapshot gelesen; alle Datenseiten behalten die Annahmegrenze. Der aktuelle
Trustport ist ebenfalls in sämtlichen tatsächlichen `materialize_fold`-Aufrufen
des Backtestworkers angebunden. Die unveränderliche Backtestbindung enthält
Trustversion und Regelversion; eine neue Prüfzeit allein verändert ihren Hash
bei Recovery nicht. Ein geänderter Truststand wird nicht stillschweigend mit
einem früher gebundenen Backtestergebnis vermischt.

`publish(...)` prüft die Fence vor und nach allen Schreiboperationen und
veröffentlicht Bericht, Befunde, Belegbindungen, Terminalstatus und Audit in
einer Transaktion. Fehler und Budgetüberschreitung veröffentlichen weder
Teilbericht noch Teilbefunde. Domainbudgets bleiben 100.000 Revisionen,
10.000 Befunde, 12 MiB Publikation und die RDR-02-Fenstergrenzen; SQLstatements
sind auf zwei Sekunden und Lockwartezeit auf eine Sekunde begrenzt.

## Unveränderlichkeit, Zeiten und Trust

Befundseiten sind nach persistiertem Index sortiert. Der HMAC-Cursor bindet
Assessment, Berichthash, Daten- und Trustversion, Trustregel, Filter und Position;
er läuft nach einer Stunde DBzeit ab. Manipulation, falscher Bericht/Filter und
Ablauf liefern CURSOR_INVALID. Die Signierinformation stammt aus dem
persistierten Cursorzustand und überlebt Service-Neustarts.

Ein späterer Widerruf schreibt einen veröffentlichten Bericht nicht um.
`currentRevocation` wird getrennt unter aktuellem Truststand projiziert; nur die
tatsächlich gebundenen Evidenz-UUIDs und exakt zugehörigen Quellen-/Schlüssel-/
Zeugenketten werden berücksichtigt. Zukünftig wirksame Entscheidungen werden
nicht als bereits wirksamer Widerruf dargestellt. Die Prüfung nutzt dieselbe
Gültigkeits-/Kompromittierungsregel wie der Evidenzreader.

`load_observations(...)` bindet Receipts an Revision-ID, Inhaltsfingerprint und
Baridentität. Die Ingestion und Commitversion gehören zur ursprünglichen
Inhaltsrevision; ein unveränderter Abruf nach +240 Sekunden verschiebt sie nicht.
Die erste und die erneute tatsächliche Empfangszeit bleiben getrennte Receipts.
Eine Commitbestätigung zählt erst, wenn ihre eigene Version und Zeit im
Datensnapshot sichtbar sind. Unbelegte Uhrenvergleichbarkeit sowie historische
Providerpublikation bleiben UNKNOWN; Fristquoten ersetzen keinen individuellen
PITnachweis. Lokale Sichtbarkeit behauptet keine externe Publikationsprüfung.

## Prüfevidenz

- `tests/integration/test_readiness_api.py`: echte Rollenports,
  authentifiziertes HTTP, Origin-/Sitzungsablehnung vor Annahme, idempotente
  Annahme und Konflikt, persistierte ACCEPTED-/SUCCEEDEDzustände, synthetisch
  ELIGIBLE und unabhängig BLOCKED, Reload, unveränderliche Pagination,
  dauerhafte HMAC-Schlüssel, Manipulation/Filter-/Assessmentbindung/Ablauf;
  Receipt- und Commitbestätigungssnapshots, ursprüngliche Ingestion und
  +240s-Duplikat. Eine unabhängige SQL-Verbindung schreibt während erzwungener
  Ein-Zeilen-Seiten eine neue Korrektur. Diese liegt nach dem angenommenen
  Versionscap, fehlt in sämtlichen gelesenen Seiten und verändert den vollständig
  mit der Referenz verglichenen Bericht nicht. Nur die tatsächliche Erstellzeit
  wird für den Referenzvergleich angeglichen und der Berichthash neu berechnet.
  Providertransport ist während Workerticks ausdrücklich gesperrt.
- `tests/recovery/test_readiness_jobs.py`: echter Prozessabbruch vor Publikation,
  Transaktionsabbruch nach Schreiboperationen vor Commit, atomarer Rollback,
  erfolgreicher neuer Fence, Ablehnung alter Fences und doppelter Publikation,
  unveränderliche Rollenrechte, drei Versuche, 32er-Queue samt Replay bei voller
  Queue sowie konkurrierende Diagnose-/Backtestclaimer und gemeinsame
  Workergrenze. Keine Brokerorders.
- Gemeinsamer PostgreSQLlauf mit den beiden bestehenden Backtest-API-/Jobtests:
  `4 passed in 129.01s`. Beide neuen Suiten bestanden auch einzeln
  (`1 passed in 36.12s`, `1 passed in 41.42s`). Die ergänzte finale
  HTTP-/Receipt-/Parallelwriter-Suite besteht mit `1 passed in 29.71s`.
- Transport-/DTO-/Relayvertrag: Hauptagent bestätigt `33 passed in 2.49s` und
  anschließend mit beiden exakten Routentests `35 passed in 5.35s` außerhalb der
  bekannten TestClient-Sandboxbeschränkung. Ein in dieser Beschränkung
  unterbrochener Lauf ist keine Prüfevidenz.
- Ruff über sämtliche RDR-05-Dateien: `All checks passed!`.

Die vollständige importierte Signatur-/Widerrufskette, die eingeschränkte
Containerlaufzeit und die Lastisolation gegenüber Pause/Kill/Reconciliation
werden zusätzlich in RDR-08 gemeinsam abgenommen. Die Diagnose führt weder
Sammlung noch Import noch Backtest automatisch aus. Reale Providerrechte,
externe historische Zeitbeweise und reale Eignung bleiben unverifiziert.

## Unabhängige Integration und Regression

Der Hauptagent prüfte Queue-/Fence-/Publikations- und HTTP-/Cursorgrenzen
unabhängig vom Implementierer. Der private Headerfehler wurde geschlossen;
fehlerhafte Eingaben erzeugen redigierte 400-Antworten. Im isolierten RDR-05-Stand
ohne die noch offenen Planoperationen bestanden alle **1505 Vertragstests in
39,12s**, einschließlich beider exakter Runtime-Routentests und sämtlicher drei
Diagnoseoperationen. Die Transportsuite bestand separat mit 33 Tests in 2,49s.

Die unabhängige gemeinsame PG-Abnahme prüfte tatsächliche importierte Referenzen:
zukünftiger Widerruf und eine fremde Quelle mit gleicher Schlüssel-ID erzeugen
keine falschen Warnungen; aktuelle Witness-/Source-Widerrufe zeigen die exakten
Entscheidungsreferenzen und aktuelle Trustversion. Originalbericht und dessen
Cursor bleiben unverändert gebunden. Keine offenen P1/P2 im geprüften Umfang.

Das strikte Paper-Provisionierungsmanifest unterstützt die exakt hinzugekommenen
0030-/0031-Rechte; vorhandene Finanzrechte bleiben byteweise als Menge geprüft.
Die tatsächliche Paper-Safetyprüfung unter laufender Diagnose bestand nach dieser
Korrektur. Gesamtunitregression: 1073 bestanden, eine Linuxprüfung unter Windows
übersprungen; Credentialscan: 1119 Dateien ohne Befund.
