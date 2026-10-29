# RRB-07 / #75: Material-Forschungsoberfläche

Apply-Auftrag vom 27.10.2026; Basis main ee64922734620a910013968da5b3745210abd360, Issuebranch codex/ptv-75-retrospective-ui. REQ-RRB-001/006/008/010/011/012; T-RRB-001/002/009/010/011/013/014/016/017/019/020.

## Ergebnis

Strict PIT bleibt voreingestellt. Retrospektive Forschung verlangt die ausdrückliche Moduswahl, eine dauerhafte Vorschau und anschließend einen eigenen Start. Formularänderungen verwerfen die Vorschau einschließlich URLbindung; verspätete GET-Antworten können sie nicht wiederherstellen. Reload rekonstruiert das gespeicherte Protokoll und dessen Bindung. Unmittelbar vor dem expliziten Start lädt die Oberfläche aktuelle Reportwarnungen erneut; ein negativer oder nicht verfügbarer Prüfstand erzeugt keinen Start-POST.

Liste, Detail, Foldresultate und Report zeigen Research Mode, Data Snapshot, Data Quality, Corporate Action Status, Result Limitations und Experiment Version. Qualitätswerte stammen aus dem wirklichen Report; während des Ladens werden keine positiven Werte erfunden. Retrospektive Manifeste und Ergebnisarrays verwenden die additiven generierten Typen. Der Fillcutoff heißt ausdrücklich simulierter Marktcutoff und behauptet keinen damaligen Datenempfang. Readiness bleibt Strict.

Der Materialdialog zeigt sechs unabhängige Qualitätsdimensionen, unveränderlichen Reporthash, Befunde und Revisionen mit gebundener Pagination und Filtern. Aktuelle Warnungen stehen getrennt vom historischen Report. Übersichtsanfragen laufen seriell innerhalb der bestehenden API-Aufnahmegrenze. Sessionverlust schließt Dialoge, entfernt Forschungsdaten und beendet Polling ohne automatische POST-Wiederholung. Der Zustand bleibt lokal; kein zusätzlicher gemeinsamer Store oder Brokerpfad.

## Prüfumgebung und Aussagegrenzen

Der Browserowner erzeugt ausschließlich eigene kurzlebige PostgreSQL-/API-/Workercontainer im internen Netzwerk. Tatsächliche Researchports ingestieren die historischen QQQ-Bars heute; Ingestionzeiten werden nicht verändert. Kalender und eine ausdrücklich synthetische Splitauthority sind lokale Testfixtures. Vier historische Ergebnisse werden über den echten Service und Worker angelegt. Der Browser nutzt die tatsächlichen authentifizierten HTTPoperationen und das gebaute Dashboard. Ein Race-Test hält lediglich einen echten GET zurück und setzt ihn unverändert fort; keine Antwort wird ersetzt. Die isolierte Testownerrolle ändert für den Lizenz-Negativtest den lokalen Status, während Browser und API keine solchen Schreibrechte erhalten.

Die SQLnachprüfung verlangt dauerhafte Erfolgs- und Blockierzustände, keine Brokerorders und keine zusätzlichen Providerabrufe. Aktivierungscodes bleiben flüchtig; Screenshots/Trace/Video sind ausgeschaltet. Ein einzelnes Reportbild wird nach verborgener Aktivierung für lokale Layoutprüfung erstellt. Der ergänzte Researchharnessmount enthält ausschließlich das öffentliche kanonische Backtestschema, das die neue DTOinitialisierung benötigt.

Produktionsbuild bestanden, zuletzt 13.619s; bestehende Bundle-/Supervisor-CSS-Budgetwarnungen. Der generierte Client und die kanonischen Verträge wurden nicht manuell verändert. Context7 wurde für Angular Material und Playwright angefragt, meldete jedoch ausgeschöpftes Monatskontingent. Verwendete Primärquellen: [Angular Reactive Forms](https://angular.dev/guide/forms/reactive-forms), [Playwright Network](https://playwright.dev/docs/network), [Material Dialog](https://github.com/angular/components/blob/main/src/material/dialog/dialog.md). Die installierten Typen wurden zusätzlich durch den Produktionsbuild geprüft.

## Ausgeführte Prüfungen

- Erste vollständige retrospektive Realbrowsermatrix: 7 passed, 47.1s; anschließend unabhängiger Review mit zusätzlichem Start-Race und dessen Reparatur.
- Bestehende Backtest-Realbrowsermatrix: 4 passed, 50.3s; unabhängige Decimalresultate, Reload, historische Blockierung und Sessionverlust, SQLnachprüfung bestanden.
- Bestehende Readiness-Realbrowserabnahme: 1 passed, 28.1s; acht ausschließlich synthetische UItests wurden im Realmodus absichtlich übersprungen. SQLnachprüfung bestanden.
- Ruff für beide geänderten Pythonharnesses bestanden; Quellscan 1247 Dateien, null Credentialmusterbefunde.

- Finale retrospektive Realbrowsermatrix: 8 passed, 52.2s, einschließlich verzögerter echter Startantwort. SQLnachprüfung bestanden. Das Reportbild wurde visuell auf lesbaren, opaken Dialog und vollständigen Kontext geprüft.
- Bestehende Research-Realbrowsermatrix: 2 passed, 13.7s; tatsächlicher Katalog, gespeicherte PIT-Bars, Filter, expliziter idempotenter Import, Reload und Sessionverlust; SQLnachprüfung bestanden.
- Schema-/Beispielprüfung: 66 Operationen, 27 Schemas, 125 Responsebeispiele und 16 retrospektive Fixtures bestanden. Strict-OpenSpec-Validierung bestanden. Kanonische Verträge und generierter Client haben keinen Diff gegen die Basis.
- Unabhängiger finaler UIquellreview: keine offenen P1/P2; ursprüngliche Befunde zu Introtext, Übersichtslast und Start-Race behoben und durch echte Browserfälle geprüft.

Echte Providerrechte, historische PIT-Eignung und Profitabilität werden durch diese synthetische lokale Abnahme nicht bestätigt. Keine Handelsaktivierung, Promotion oder Archivierung.

Vollständige finale Vertragsuite seriell: 1899 passed, 56.86s; bestehende Starlette/httpx-Abkündigungswarnung. Ein erster Lauf unter paralleler Browserlast hatte 1898 passed und einen Schedulingfehler im unveränderten Heartbeat-Test; die vollständige serielle Wiederholung bestand ohne Teständerung. Der Sandboxlauf war zuvor bei Transporttests hängen geblieben und wurde abgebrochen; die Ausführung mit erlaubter lokaler Socketnutzung bestand.
