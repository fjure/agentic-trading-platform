# Retrospektive Research-Backtests

## Lieferstand RRB-01

Der Apply-Auftrag vom 27.10.2026 beginnt mit RRB-01/#69. Dieses Paket liefert additive Verträge, Schema-/Negativtests und den mechanisch generierten Angularclient. Die neuen Vorschau-/Reportoperationen werden erst mit RRB-06 als tatsächliche Runtimefunktionen abgenommen. Eine gültige Vertragsfixture beweist weder historische Datenqualität noch ein lauffähiges Experiment.

## Domainlieferung RRB-02

Die getrennte retrospektive Datenselektion und ihre unveränderlichen Manifest-/Replaymodelle sind implementiert. Sie bewahren tatsächliche Zeitfelder und lassen neue Korrekturen nur in neue Snapshots einfließen. Feste Strict-Identitäten bleiben als Regression abgesichert. Es gibt dadurch noch keinen neuen lauffähigen HTTP-/Workerpfad; Qualitätsreport, Persistenz und gemeinsame Simulation folgen ihren eigenen Paketen. Nachweis: `docs/evidence/rrb-02.md`.

## Simulationslieferung RRB-04

Strict und Retrospektiv verwenden denselben Decimal-Simulator und dieselben Strategien/Metriken mit getrennten validierten Dateneingängen. Öffentliche Aufrufe prüfen die vollständige Manifestbindung; die vier Strict-Hashorakel bleiben unverändert. Eine neue Worker-/HTTPausführung ist damit noch nicht freigegeben oder implementiert. Nachweis: `docs/evidence/rrb-04.md`.

## Qualitätslieferung RRB-05

Der reine Reliabilityassessor liefert sechs getrennte Dimensionen und dieselben snapshotgebundenen Regeln für Vorschau und Finalreport. Bekannte Maßnahmenkonflikte, notwendige Datenlücken und Preisbrüche ab 20 Prozent blockieren; fehlende Belege bleiben sichtbar unbekannt. Ein positiver Report verlangt das exakt gebundene Manifest. Nachweis: `docs/evidence/rrb-05.md`. Worker/API und Oberfläche folgen ihren eigenen Paketen.

## Persistenzlieferung RRB-03

Die additive Speicherung bindet konkrete Datenrevisionen, Kalender, Truststand, Build und tatsächliche lokale Lizenzreceipt an denselben Vorschau-Snapshot. Übersicht, Befunde und Revisionsreferenzen bilden einen unveränderlichen gemeinsamen Report-Envelope; der vollständige Fingerprint bindet auch später paginierte Einträge. Runs, Strict-Readiness und retrospektive Vorschauen teilen Queue100, oldest-job-Admission und Lease-/Workergrenzen. Altworker können neue Varianten ohne Policykontext nicht übernehmen. Nachweis und tatsächliche Prüfergebnisse: docs/evidence/rrb-03.md.

Der schmale Lizenzport bestätigt keine persönlichen Providerrechte. Vor neuer Ausführung und positiver Publikation muss die Workerintegration aktuelle negative Rechte/Truststände separat prüfen; historische Receipts und Reports werden dabei nicht umgeschrieben. Vorschau und Finalreport bleiben getrennte unveränderliche Versionen. RRB-03 allein implementiert noch keinen neuen HTTP-/Workerpfad.

Rollback stoppt neue Erstellung/Dispatch und behandelt nichtterminale neue Jobs vor Rückkehr alter Worker ausdrücklich. Neue Varianten werden niemals zu Strict umgedeutet; veröffentlichte Daten/Reports bleiben erhalten. Entwicklungstests verwenden einen PID-1-Init für PostgreSQL, damit verwaiste Health-/Execprozesse keinen Server-Neustart auslösen. Isolierte Testvolumes ersetzen keine oder reparieren keine historischen Nutzerbelege.

## Worker- und APIintegration RRB-06

Die drei Vorschau-/Reportrouten sind einschließlich öffentlichem BacktestClient und privatem SQLservice implementiert. Der bestehende Dispatcher verarbeitet retrospektive Jobs im selben begrenzten Pool wie Strict-Backtests und Strict-Readiness. Dispatch bleibt standardmäßig deaktiviert. Eine Vorschau startet keine Simulation; ein späterer Lauf benötigt ihre exakte Protokoll-/Snapshotbindung. Crashwiederaufnahme verwendet die gespeicherten Referenzen und lädt keine neuen Kursstände nach. Prüfevidenz und Abnahmestand: docs/evidence/rrb-06.md.

Vor Ausführung und positiver Veröffentlichung prüft der Worker aktuelle negative Rechte, relevante Trustwiderrufe und inzwischen bekannte strukturelle Maßnahmen erneut. Migration 0034_retrospective_gate schützt auch direkte SQL-Publikation. Ein separat dauerhaft blockierter Lauf erhält kein Ergebnis; sein finaler Snapshotbericht darf unverändert UNKNOWN oder explorativ bleiben. Historische Reportbefunde und Lizenzreceipt werden nicht nachträglich umgeschrieben.

Reportseiten enthalten getrennte aktuelle Warnungen mit tatsächlicher Prüfzeit. Spätere Änderungen gebundener Bar-, Kalender-, Evidenz- oder Qualityidentitäten erzeugen EX_POST_REVISIONS, ohne die alte Snapshotwiedergabe zu ändern. Aktuelle Lizenz-/Trust-/Maßnahmensperren blockieren neue Ausführung; ein nicht verfügbarer Prüfport ergibt keine positive Freigabe. Die Korrekturabfrage liefert lediglich einen booleschen Hinweis über einen engen SQLport. Referenzpagination bindet Report, Abschnitt, Filter und vollständigen gespeicherten Fingerprint; ein fremder Cursor ist ein Konflikt.

Der Runtimebuild enthält das kanonische Backtestschema und die gepinnte Produktionsabhängigkeit jsonschema. Keine Erweiterung von Broker-/Jev-/Providerzugriffen, Credentials, Netzwerken, Mounts oder Finanzrechten. Schema-Downgrade löscht keine Evidenz: Rollback bleibt der oben beschriebene Stop-/Drainablauf.

## Researchmodi und Ein-/Ausgaben

Die Materialoberfläche ist in RRB-07 implementiert und mit echter lokaler PostgreSQL-/API-/Browserkette geprüft; Evidenz: docs/evidence/rrb-07.md. Forschungsmodus ausdrücklich wählen, retrospektive Vorschau erstellen, deren Blocker und Einschränkungen prüfen und erst anschließend die Simulation beauftragen. Jede Eingabeänderung verwirft die Vorschau. Dauerhafte Links verwenden backtestAssessment, backtestRun und backtestReport; Reload stellt Protokoll beziehungsweise Ergebnis wieder her. Vor jedem expliziten retrospektiven Start werden aktuelle Reportwarnungen erneut geprüft. Ein nicht verfügbarer Prüfstand erlaubt keinen Start.

Der Qualitäts- und Grenzenreport bietet paginierte Befunde und Revisionen mit Kategorie-/Foldfilter. Historischer Reporthash und aktuelle Warnungen bleiben getrennt. Sessionverlust entfernt Reportdaten und beendet Abrufe; eine neue Sitzung löst keine automatische Auftragserstellung aus. scripts/test-backtest-browser.ps1 prüft bestehende und retrospektive Browserfälle in jeweils eigener isolierter Testumgebung; -RetrospectiveOnly ist die fokussierte neue Matrix.

Bestehende `POST /api/v1/research/backtests/runs`-Requests bleiben ohne Modusauswahl Strict PIT. `STRICT_PIT` ist auch ausdrücklich auswählbar. Die bisherige historische Beleg-/Fehlersemantik, Zeitgrenzen und Idempotenz bleiben erhalten. Ein blockierter Strict-Lauf löst niemals automatisch einen retrospektiven Lauf aus.

`RETROSPECTIVE_EXPLORATORY` erfordert eine ausdrücklich gewählte Policyversion und die Referenz einer Vorschau mit Eingabe-/Snapshotbindung. Unterschiedliche Modi sind unterschiedliche Nutzlasten; wiederverwendete Idempotenzkennungen mit anderem Inhalt sind Konflikte. Freie Programme, URLs, Strategien außerhalb des Katalogs und unbekannte Felder/Versionen sind unzulässig.

| Operation | Zweck | Requirements |
|---|---|---|
| createBacktestAssessment / POST `/api/v1/research/backtests/assessments` | begrenzte dauerhafte Vorschau, 202 und Idempotency-Key; keine Simulation | REQ-RRB-002/008/009/010/011/012 |
| getBacktestAssessment / GET `/api/v1/research/backtests/assessments/{assessmentId}` | Status, Snapshot und Reportreferenz | REQ-RRB-002/008/012 |
| getBacktestReliabilityReport / GET `/api/v1/research/backtests/reliability-reports/{reportId}` | unveränderlicher strukturierter Bericht mit höchstens 200 Einträgen je Seite | REQ-RRB-008/009/010/011/012 |
| createBacktestRun und bestehende Lauf-/Ergebnisoperationen | expliziter Modus und Bindung, bestehende Strict-Anfragen kompatibel | REQ-RRB-001/004/005/006 |

Alle Operationen verwenden LocalSession und die bestehenden Origin-/CSRF-/Fehlergrenzen. Ungültige Eingaben sind 400, fehlende Sitzung 401, verweigerter Zugriff 403, fehlende Referenzen 404, Idempotenz-/Snapshotkonflikte 409 und nicht verfügbarer Dienst 503. Vorschau ist keine Handelsfreigabe und startet selbst keinen Backtest.

## Aussagegrenzen

Ergebnisse bleiben raw/IEX/15Min für SPY oder QQQ, price-only und explorativ. Snapshot/Revisionen, echte Ingestion, simulierte Entscheidung und heutige Datensatzerstellung sind getrennte Zeitbegriffe. Heutige Providerkorrekturen werden im eingefrorenen Datensatz gebunden und begründen keine damalige Verfügbarkeit. Alte Manifeste werden nicht umgeschrieben.

Der versionierte Reliability Report trennt Datenintegrität, Kalender, Kapitalmaßnahmen, historische Verfügbarkeit, Feedabdeckung und Lizenzstatus. Unbekannte Nenner und ungeprüfte Dimensionen werden nicht mit erfundenen Werten gefüllt. Ein hoher simulierter Gewinn verbessert keine Qualitätsbewertung.

APIkompatibilität: Strict-Details verwenden unverändert `manifest`, retrospektive Details das zusätzliche `retrospectiveManifest` bei altem `manifest=null`. Strict-Foldzusammenfassungen bleiben unter `items`; retrospektive Summaryseiten liefern dort keine Strict-Objekte, sondern eigene `retrospectiveItems` mit ehrlichem Maßnahmenstatus. Explizite retrospektive Antworten verlangen vollständigen Modus-/Policy-/Versions-/Snapshot-/Report-/Einschränkungskontext. Die alten Pflichtfelder und Erfolgsregeln bleiben erhalten.

Retrospektive Fillseiten verwenden ebenfalls `retrospectiveItems`. Ihre Fills tragen `simulatedMarketCutoff` statt eines scheinbaren historischen `observationCutoff`; echte Aufnahmezeiten bleiben in der Datenevidenz erhalten. Gemeinsame Equitypunkte sind simulierte Ledger-/Marktwerte und enthalten keine Verfügbarkeitszertifikate.

Bekannte Splits, nicht modellierte strukturelle Maßnahmen, widersprüchliche Evidenz, notwendige fehlende Bars und Kalenderfehler blockieren. Unbekannte Maßnahmen bleiben UNKNOWN mit Einschränkungen; Bardividenden sind ausgeschlossen. Der konservative Preisbruchfilter ab 20 Prozent ist kein Splitdetektor. Es gibt keinen Total-Return- oder Profitabilitätsnachweis, keine automatische Promotion und keine Paper-/LIVE-Aktivierung.

Persönliche Providerrechte und echte historische Dateneignung bleiben ungeprüft. Ein technischer Zugang oder eine lokale Lizenzdeklaration bestätigt diese Rechte nicht. Keine neuen Providerabrufe, Rohdatenexports, Credentials oder Handelsberechtigungen werden durch diese Vertragslieferung eingeführt.

## Abnahme und spätere Runtime

`scripts/contract_stages.py` nimmt ausschließlich die drei neuen Operationen bis Abschluss von RRB-06 vom vollständigen Runtime-Routenvergleich aus. Fehlende oder doppelte RRB-06-Taskzuordnung blockiert die Prüfung; nach dessen Abschluss werden alle drei echten Routen automatisch verlangt. Alle Bestandsrouten bleiben sofort prüfpflichtig. Keine Serverstubs.

Vertragsprüfung: `python scripts/validate-contracts.py`, `python scripts/check-contract-breaking.py --base-ref main`, `pytest tests/contracts`, `./scripts/check-angular-client.ps1`. Clientänderungen ausschließlich mit `-WriteClient`, zweimalige Generierung und TypeScriptprüfungen. Tests und Quellenachweise pro Paket stehen unter `docs/evidence/rrb-NN.md`. Reale PG-/HTTP-/Browser-/ACL-/Lastabnahme folgt in RRB-03/06/07/08 gemäß OpenSpec.
