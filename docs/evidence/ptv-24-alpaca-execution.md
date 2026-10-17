# PTV-24: Dauerhafte Alpaca-Executionbindung

Prüfstand2026-10-17; REQ-RSK-003/004/005/007/008/009 und REQ-RUN-001/002/005/006. Der Nutzer hat die zusätzlichen PTV-24/25 vor PTV-21 ausdrücklich freigegeben. Bestehende IDs bleiben erhalten; der Plan enthält25LeafTasks. Die Erweiterung ist mit genau zwei neuen Issues#24/#25, aktualisierten PTV-21-Abhängigkeiten und vollständigem25-Issue-Read-back synchronisiert. Identische Wiederholung: null neue Issues und null Updates. Keine echten Broker-/Jev-Requests oder Orders ausgeführt.

## Implementierung und Grenze

Nur die exakten Fake- und Alpaca-Paperadapter sind zulässig. Alpaca benötigt eine Execution-eigene Bindung zum selben Ledger, internen Konto und Adapter. Private Belege verbinden unabhängig gelesene Konto-/Asset-/Sitzungsmetadaten beider Provider. Die kanonische Trading-Capabilityversion wird in den Risikokontext übernommen; die unterschiedliche Marktversion bleibt separat gebunden und wird erneut verglichen. Keine künstliche Gleichsetzung unabhängiger Hashes. Explizite reguläre beziehungsweise kontinuierliche Sitzungsfähigkeit gilt passend zum Markt. Worker- und öffentliche DTOs erhalten keine Anbieter-UUIDs.

Migration0008 ergänzt ausschließlich additive private Pins/Provenienz sowie die kontinuierliche Sitzungsfähigkeit. Execution besitzt SELECT/INSERT auf die unveränderlichen Bindingtabellen; Worker und Control erhalten keine Leserechte. Einfügen verlangt passende Auditidentität, Version, Akteur und Trace in derselben Transaktion. Bestehende eingefrorene Migrationen bleiben unverändert. Metadatenproofs benötigen keine neuen Kerzen; der getrennte Markt-/Riskpfad behält seine strengen Kerzen- und Frischeprüfungen. Frische wird nach den Lesezugriffen gegen die aktuelle Datenbankzeit geprüft, einschließlich der ältesten erfassten Beleggrenze.

Die erste Bindung, ein Providerwechsel oder eine Änderung gebundener Capabilityversionen invalidiert bisherige Recoveryautorität atomar über den bestehenden auditierten Safety-Latch und dessen typisierte Outbox. Eine frühere Fake-Reconciliation ist keine Alpaca-Freigabe. HALTED bleibt erhalten; der normale Alpaca-Recovery-/Aktivierungspfad bleibt bis PTV-25 geschlossen.

SUBMITTING, Report, Audit und Outbox werden vor dem einzigen POST bestätigt. Danach hält Execution den Kontolock über aktuelle Gates, private Proofprüfung, erneute Gates und Broker-I/O. Eine definitive lokale Ablehnung darf eine Reservierung nur mit weiterhin gültigem Owner/Fencing freigeben. Nach möglicher Übermittlung erzeugen unklare Antworten UNKNOWN mit gehaltener Reservierung und Pause. BaseException oder fehlschlagender SQL-Commit lässt den bereits bestätigten SUBMITTING-Anker bestehen. Restore bindet alle eigenen persistierten Alpacaorders einschließlich SUBMITTING und terminaler Zustände als versucht, prüft Identitäten/Adapter/Belege und führt dabei keine Provider-I/O aus. Ein späterer unklarer 404 führt nicht zu einem zweiten POST.

Auch ein gültiger Alpaca-ACK behauptet keine vollständige Reconciliation: sein Report bleibt PENDING und fee=null. Für definitive pre-I/O-Ablehnungen werden keine hypothetischen Brokerkosten angenommen. Fill-/Gebührenbuchung, vollständiger Bestand und tatsächlich freigabefähige Alpaca-Recovery folgen ausdrücklich PTV-25. PTV-21 übernimmt danach Prozessverdrahtung und Deploymentnachweise.

## Prüfungen

- Neun neue PostgreSQL-/MockTransport-Szenarien nach atomarer Latchkorrektur: **9passed in209.74s**. Konto-/Assetmismatch, private SQL-Rechte/Unveränderlichkeit/Auditpflicht, Owner/Fencing, erste Bindung/gesperrte Aktivierung, bestätigter Submit, Timeout nach Annahme, Crash vor/nach I/O und echter nachgelagerter SQL-Commitfehler.
- Ein zusätzlicher PostgreSQL-Lauf nach der ACK-Korrektur: **1passed in23.84s**, Report PENDING und fee=null bestätigt.
- Der MockTransport-Handler liest SUBMITTING über eine unabhängige Datenbankverbindung, bevor er den synthetischen POST annimmt. Ein deferred PostgreSQL-Constrainttrigger erzwingt einen Commitfehler nach Annahme; Restore plus versteckter Lookup behält SUBMITTING und sendet keinen weiteren POST. Fixture-RUNNING-Autorität wird ausdrücklich nur im Test nach der nachgewiesenen Providerpause gesetzt; dies ist keine Produktionsaktivierung.
- Abschließende kombinierte lokale Suite: **419 passed in 7.05s**; gezielter Lauf Binding/Sync/Audit **25 passed in 1.03s**. Eine bestehende Starlette-/HTTPX-Deprecationwarnung, kein Fehler.
- Ruff auf geänderten Quellen bestanden. OAS3.1.2/18Operationen, zehn interne Schemas und Beispiele valide; oasdiff gegen Basismain e9bff39 ohne Änderung.
- Zwei65-Dateien-Generierungen mit OpenAPI Generator7.26.0 bytegleich, vorhandener Angularclient ebenfalls bytegleich; TypeScript erfolgreich. Keine generierte Datei geändert.
- OpenSpec strict bestanden; 40 Requirements und 25 eindeutige LeafTasks mit geordnetem Abhängigkeitsgraphen valide. Quellscan: 344 Dateien, null Credentialmuster. Issue #24 abgeschlossen; vollständiger 25-Issue-Read-back und identische Wiederholung mit null Writes bestanden. Test-PostgreSQL anschließend gezielt gestoppt.
- Bestehende Fake-Submit-/Ledger-/Migrationsregressionen: **13 passed in 309.54s**; 100 parallele Budgetvorschläge: **1 passed in 24.64s**. Safety-/Isolation-/Pause-/Resume-Prüfungen: **26 passed, 1 deselected in 556.45s**. Der getrennt bestandene Budgetrace war im Safety-Keywordfilter nicht enthalten.

## Review und Befundkorrekturen

Begrenzte Subagenten bearbeiteten getrennt Binding/Migration, SQL-Submit/Risk sowie unabhängige PostgreSQL-Tests. Gegenseitige Sourceprüfungen fanden eine fehlende älteste Beleggrenze; diese wurde korrigiert. Der Hauptagent fand die Frischeprüfung vor statt nach Providerreads, die unbeabsichtigte Übernahme einer Fake-Recoveryfreigabe und fehlende öffentliche Abbildung neuer Binding-Audits. Korrekturen sind durch Sourceprüfung und tatsächliche Tests belegt. Ein späterer Subagentenlauf endete am Nutzungslimit; sein vollständiger finaler Review wird nicht behauptet. Der Hauptagent trägt den Abschlussreview.

Der erste PostgreSQL-Lauf scheiterte in Migration0008: SQLAlchemy interpretierte eingebettete Doppelpunktliterale als Bindparameter. Der Trigger verwendet nun getrennte Literalverkettung; ein Test prüft alle Upgrade-DDL-Statements auf versehentliche Bindparameter. Fünf Fälle des ersten gemischten Laufs scheiterten, drei nach der Korrektur bereits ausgeführte Fälle bestanden; die vollständigen Wiederholungsläufe oben sind maßgeblich. Die erste Issue-Erweiterung sah neu erzeugte Issues verzögert in der Gesamtliste; der idempotente Wiederanlauf übernahm deren eindeutige Marker ohne Duplikate. Der Helfer wiederholt jetzt ausschließlich begrenzte Read-backs nach Erzeugung und niemals die Erzeugung selbst.

## Primärquellen und verbleibende Grenzen

Unverändert HTTPX0.28.1 und Alpaca Trading-APIv2. Offizielle Dokumentation am2026-10-17 über Context7 `/websites/alpaca_markets_us` geprüft: [Activities](https://docs.alpaca.markets/us/docs/account-activities), [Crypto-Gebühren](https://docs.alpaca.markets/us/docs/crypto-fees), [Paper-Simulation](https://docs.alpaca.markets/us/docs/paper-trading). FILL-Belege und nicht zwingend orderbezogene FEE/CFEE verlangen getrennte Autorität; aus dem Paperhinweis auf nicht simulierte regulatorische Gebühren wird keine pauschale Nullgebühr für alle Profile abgeleitet. Diese Belegauswertung wird in PTV-25 implementiert.

Keine echte Kontoberechtigung, Anbieterqualität, externe Ausführung oder Deploymentisolation ist damit zertifiziert. Der neue SQL-Submitpfad ist offline geprüft; normale Alpaca-Aktivierung bleibt gesperrt. PostgreSQL-Fake-Prozessprüfungen ersetzen weder PTV-25 noch PTV-21.

## Gitnachweis

PTV-24 Gitintegration am 2026-10-17: Issuebranch codex/ptv-24-alpaca-execution, Commit 005123eb9b28c85ef6cd5d74850bd7f21ee61231; regulärer Merge-main abcd034ff82c40f6c04a9d17eaed2ef05f1c99ff. Remote-SHAs und Ancestry aller 21 integrierten Issue-Branches (PTV-01..20 plus PTV-24) verifiziert. Issue #24 completed, vollständiger 25-Issue-Read-back und Wiederholung mit null Writes. Die CI des Implementierungsmerges läuft zum Dokumentationszeitpunkt; ihr Erfolg wird noch nicht behauptet. Spätere reine Evidenzcommits dürfen main weiterführen; Zuordnung in docs/development/git-issue-integrations.json.
