# QSL-07 Worker und authentifizierte API

Issue #83. QSL-07 verbindet die 15 OpenAPI-Operationen mit der isolierten
Control-Rolle und dem gemeinsamen Backtest-Dispatcher. Der private Relay
verwendet eine feste Zieladresse und einen serverseitig abgeleiteten
Session-Scope für Cursor. Audit und Metriken erfassen feste Operations- und
Fehlercodes, gebundene Zeilenzahlen, Dauer und Trace-ID.

Migration 0036 persistiert den ersten Feasibility-Beleg pro Definition und
Idempotenzschlüssel. `INVALID` bleibt abrufbar und besitzt keine Startfreigabe.
`VALIDATED` referenziert die unveränderte 0035-Startbindung. Die statischen
ACLs begrenzen den neuen Schreibport auf die zuständige Control-Rolle. Der
laufende negative Datengate wird als separate Warnung projiziert.

Die Validation-Auswahl bindet einen aus der gespeicherten Phase abgeleiteten
Hash für Seed, Outcome, exakte Entscheidungsrevisionen und Build. Die
Validation-Resultatlinks behalten ihre echten Run- und Reliability-FKs.
Der fachliche Auswahlhash hängt dadurch nur von den für Validation gebundenen
Eingaben ab. Equity und Drawdown binden zusätzlich die Kandidatenrolle. Der
Foldvergleich führt die getrennten Aktivitätsverteilungen aus D6 mit.

## Ausgeführte Prüfungen

- Der isolierte PostgreSQL-Lauf für Draft-/Validierungsreplay und Recovery
  bestand mit zwei Testzielen in 188,50 s. Er injiziert Prozessabbrüche nach
  dem Commit aller Auswahlentscheidungen, nach dem ersten Test-Child und
  nach dem finalen Reportcommit. Der dritte Versuch läuft über den normalen
  Dispatcher. Die Simulation prüft per Poolzähler, dass sie ohne ausgecheckte
  SQL-Verbindung läuft.
- Der echte API-Harness bestand mit einem Testziel in 41,21 s. Er prüft
  Authentifizierung, Draft und Replays, belegten Strict-Kalender und fehlenden
  Kalender, gebundene Startrequests, fremde und manipulierte Cursor sowie den
  gewichteten Queuepool mit 33 Batches zu drei Slots.
- Ein kombinierter PostgreSQL-, Worker-, HTTP- und Restore-Test bestand mit
  einem Testziel in 152,50 s. Er führte Retrospective mit dokumentierter
  Nichtwahl und Strict PIT mit Auswahl aus. Beide Batches publizierten je
  zwölf gebundene Resultatlinks. Abgerechnet wurden 30 beziehungsweise 138
  Barschritte. Eine zusätzliche Retrospective-Runde mit gleicher Definition
  und korrigiertem späteren Testschluss änderte die OOS-Equity der Buy-and-Hold-
  Referenz. Der Validation-Link und beide Auswahlhashes blieben identisch.
  Der frühe globale Block startete keine Kandidaten. Ein injizierter Fehler
  des ausgewählten OOS-Kandidaten publizierte genau dessen `FAILED`-Link und
  erhielt acht vollständige Links sowie unveränderte Referenzfehlerzahlen.
- Das Runtime-Image wurde lokal gebaut. Der Smoke lief ohne Netzwerk und
  Mounts und prüfte den Schemaimport, den Source-Fingerprint und den
  standardmäßig deaktivierten Dispatcher.
- Zwei vollständige Generierungen des Angular-Clients über 370 Dateien waren
  bytegleich. Client und Decision-, Paper- und Research-Fixtures bestanden die
  TypeScript-Prüfung. Der Angular-Produktionsbuild bestand mit den vorhandenen
  Budgetwarnungen für das Initialbundle und das Supervisor-Stylesheet.
- Die vollständige Vertragssuite ergab **2.331 bestandene Tests**. Redocly
  und die Schema-/Beispielvalidierung bestanden, einschließlich aller 60
  Quant-Fixtures. `oasdiff` gegen `ba08ea7` und `379ebff` meldete jeweils null
  Fehler und eine Readiness-AllOf-Warnung. Der betroffene Legacy-Pfad und alle
  48 von ihm erreichbaren Vertragskomponenten sind exakt gleich geblieben.
- Der unabhängige Nachreview bestätigte die phasenlokale Auswahlidentität,
  die SQL-Barriere bei OOS-Teilfehlern und die korrigierte Vorabschätzung.
  Das Neuner-Orakel ergibt 9.510 Schritte und 12.300.288 geschätzte Bytes bei
  1.150 Revisions- und Metadatenzeilen, davon 80 Metadatenzeilen. Die Messung
  des gesamten Neuner-Laufs unter Containerlast ist Teil von QSL-09.

Der abschließende Storage- und API-Lauf nach dem SQL-Nachreview bestand
mit **zwei realen PostgreSQL-Testzielen in 139,74 s**. Er bestätigte das
Replay einer 0035-Freigabe mit unveränderter Approval-ID und unverändertem
Hash, die Abweisung manipulierter Historien und Budgetwerte sowie den
direkten SQL-Negativfall mit gefälschtem phasenlokalem Evidenzhash. Der
unabhängige SQL-Nachreview schloss die gemeldeten P1/P2-Befunde.
Der isolierte Abschlusslauf für Storage und Restore bestand in 96,06 s.
Er berücksichtigt den vorübergehenden `UNAVAILABLE`-Fall unter konkurrierender
Last und bestätigt den anschließenden identischen Draft-Replay. Die beiden
bestehenden PostgreSQL-API-Regressionen und die Wiederanlaufprüfung bestanden
auch in der vorherigen gemeinsamen Testmatrix.
