# RDR-01: Diagnose-, Sammlungs- und Offlineverträge

Stand 26.10.2026. Gesonderter Apply-Auftrag für research-data-readiness erteilt, Beginn mit
RDR-01/#60. Issuebranch: `codex/ptv-60-readiness-contracts`. Verifizierte Ausgangsbasis von
HEAD und origin/main: `f829a1f53678f7e46d247a1ef6e1c5429deed154`.

## Implementierter Umfang

Fünf additive HTTPoperationen und 45 benannte neue Komponenten ergänzen den kanonischen
OpenAPI-3.1.2-Vertrag. Gesamt: 63 Operationen und 216 Komponenten. Die ursprünglichen
296689 Vertragsbytes bleiben unverändert: Entfernen des neuen Pfadblocks vor components
und des angehängten Schemablocks ergibt bytegenau den Git-Basisvertrag.

Die geschlossenen DTOs bilden fünf unabhängige Vertrauensdimensionen, vier Zulässigkeits-
und getrennte Ausführungszustände, Snapshot-/Policy-/Trustversionen, paginierte Befunde,
sechs Zeitbegriffe, Latenzquantile, explizite Zähler/Nenner, Beobachtungs-/Strict-PIT-Fenster
und separate aktuelle Widerrufshinweise ab. Planänderungen besitzen Idempotenz und
expectedVersion; Defaultpläne sind DISABLED/Version0, Globalgates bleiben eigenständig.

Das separate geschlossene Offlinebündelschema beschreibt normalisierte Kalender-,
Qualitäts- und Kapitalmaßnahmenbelege mit signierter Quelle, keyId, signedAt, Revision,
Intervall und Vorgängerbindung. RFC8785-UTF8-v1 und abgetrennte Ed25519-Signaturen sind
festgelegt. Optionale unabhängige Zeitnachweise unterscheiden Existenz von Veröffentlichung.
Caller-Trustanker, Schlüsselmaterial, Ingestion-/Commitwerte und freie Felder sind verboten.

36 gültige HTTPfixtures, zwei gespeicherte HTTPnegativbeispiele und sieben synthetische
Bündelbeispiele ergänzen bestehende Beispiele. Der Validator prüft auch diese neuen
Verträge. Das Clientscript kompiliert alle 36 HTTPbeispiele als konkrete TypeScripttypen.
Generierter Code wurde ausschließlich mit dem gepinnten Generator erzeugt.

## Zweistufige Abnahme

Der Nutzer hat am 26.10.2026 ausdrücklich entschieden:
„Zweistufige Abnahme festhalten: RDR-01 mit Vertragssuite abschließen; vollständigen
Routentest bei RDR-05/06 erzwingen.“

Vor dieser Entscheidung scheiterten die beiden bestehenden exakten Routentests ausschließlich an
den fünf neuen Operationen, deren Runtime in RDR-05/06 geplant ist. scripts/contract_stages.py
enthält ausschließlich diese fünf benannten Zuordnungen: alle drei Diagnoseoperationen zu
RDR-05 und beide Planoperationen zu RDR-06. Solange das jeweilige Runtimepaket offen ist,
prüfen beide Tests dessen zukünftige Routen noch nicht; ein abgeschlossener Task aktiviert die
vollständige exakte Erwartung automatisch. Fehlende/mehrdeutige Taskzeilen scheitern. Sechs
persistierte Negativ-/Aktivierungsfälle sichern diesen Mechanismus ab. Alle bisherigen Routen
werden weiter exakt geprüft. Kein Runtime-Stub wurde ergänzt.
Die MDR-Vertragssuite prüft ihre ursprünglichen sieben Operationen nun über die expliziten
unveränderten Pfade; eine eigene RDR-Suite prüft alle fünf additiven Operationen.

## Ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| validate-contracts.py | bestanden: 36 neue HTTP-/7 Bündelbeispiele, 27 versionierte Schemas, 26 primäre und 107 eingebettete Antwortbeispiele, bestehende Negativfälle |
| Redocly 2.62.1 | bestanden, keine Warnungen |
| oasdiff 1.33.0 gegen echte Ausgangsbasis | semantic-diff-passed, keine Breaking Changes |
| check-angular-client.ps1 -WriteClient | zwei bytegleiche Generierungen über 251 Dateien; alle konkreten TypeScriptfixtures bestanden |
| Angular 22.2.2 Produktionsbuild | bestanden; zwei Budgetwarnungen: Initialbundle 896.68kB gegen Warnschwelle500kB, bestehendes Supervisor-CSS7.12kB gegen Warnschwelle4kB |
| Gezielte Vertrags-/Bestandsmatrix | 565 bestanden: readiness_examples, readiness_bundle, backtest_examples, research_contracts, paper_workflow_contracts |
| Vollständige Contractsuite einschließlich beider exakten Runtime-Routenabgleiche | 1472 bestanden, eine bestehende Starlette/httpx-Deprecationwarnung; alle fünf neuen Routen bleiben taskgebunden zwingende Folgeabnahme |
| OpenSpec validate --all --strict | 17 bestanden, 0 fehlgeschlagen |
| Ruff für neue/geänderte Vertragstests | bestanden |
| SourceScan | 1072 Quellen, 0 Credentialmuster |
| git diff --check | bestanden vor Evidenzabschluss |

Die vollständige Contractsuite bestand außerhalb der Sandbox in 19.69s. Lokale ASGItransporte
blieben innerhalb der Sandbox stehen; die abgebrochenen Sandboxtestläufe gelten nicht als
bestanden. Keine Testdoubles wurden durch reale Provider ersetzt. Ein weiterer vollständiger
Clientdriftlauf bestätigt alle251 Dateien gegen beide frischen Generierungen einschließlich
TypeScriptkompilation.
Ruff auf dem historischen validate-contracts.py meldet bestehende
Stilbefunde; dessen unveränderte Altzeilen wurden nicht pauschal umformatiert.

## Unabhängiger Review und behobene Befunde

Begrenzte getrennte Zuständigkeiten: HTTPverträge/Beispiele, Offlinebündel/Tests sowie
ein separater lesender Contract-/Safetyreview; Hauptagent integriert und prüft. Der Review
fand eine fehlende signierte Schlüssel-ID/Signierzeitbindung. Beides ist jetzt Teil der
signierten Nutzdaten; Gleichheit mit dem Signaturumschlag bleibt Pflicht des späteren Importers.
Falsch geschriebene Providerwerte und inkonsistente positive Beispielmetriken wurden korrigiert.
Der positive Zehn-Bar-Fold, seine maximalen Fenster und 20s+5s-Latenzen sind konsistent.
Ein Widerrufshinweis erhält den ursprünglichen positiven Bericht unverändert.

Die erste Generatorprobe fand verlorene null-Typen bei anyOf; direkte nullable Typunionen und
die bereits im Projekt verwendete doppelte Negation der Validierungsbedingungen beheben das
im kanonischen Vertrag. Frische Abnahme: 338 RDR-Tests sowie 33 zusätzliche unabhängige
Negativprüfungen bestanden, keine offenen P1/P2. Keine manuelle Clientkorrektur.

## Grenzen und Safety

Diese Lieferung bestätigt Vertragsstruktur und Clientkompatibilität. Schema-gültige Platzhalter-
signaturen bestätigen keine Kryptografie, historische Publikation oder realen Nutzungsrechte.
1MiB vor dem Parsen, Digest-/Signaturprüfung, Quellen-/Schlüsselregister, Zeitreihenfolge und
feldübergreifende Bindung bleiben RDR-09; atomare Persistenz und Rollen RDR-03. Der Importer
wird nicht als implementiert ausgegeben. Produktion bleibt ohne Trustanker.

Assessorparität, Scheduler-/Pausebarriere, SQL-/HTTP-/Browserintegration und Lastisolation bleiben
Pflichten der jeweiligen Folgepakete. Risk, Execution, Reservierungen, Reconciliation, Orders,
Jev und Credentialmounts wurden nicht geändert; keine Provideraufrufe oder Brokerorders.
LIVE bleibt unerreichbar. README.md und apps/dashboard/angular.json waren bereits geändert
und gehören nicht zum RDR-01-Commit. Der Build verwendet den vorhandenen Arbeitskopiestand.

Context7-Auflösung wurde zuerst versucht; Monatskontingent erschöpft. Für die Schemaarbeit
wurden stattdessen die offiziellen [OpenAPI-Referenzen](https://spec.openapis.org/oas/v3.1.2.html)
und [JSON-Schema-Objektreferenzen](https://json-schema.org/understanding-json-schema/reference/object)
verwendet. Keine globale MCPkonfiguration oder Authentifizierung geändert.

Die abschließende unabhängige Prüfung der zweistufigen Abnahme empfiehlt Integration: keine
offenen P1/P2; alle bestehenden Operationen bleiben exakt geprüft, Abschlüsse von RDR-05/06
fordern ihre jeweiligen echten Routen automatisch.

## Issue- und Gitnachweis

Alle neun Issues wurden über den offiziellen GitHubconnector gelesen und per repositorygebundener
Suche eindeutig bestätigt. Der MCP-Schreibversuch lieferte403 Resource not accessible by integration;
der bereits autorisierte GH-CLI-Fallback aktualisierte ausschließlich #60/#64/#65. #60 ist geschlossen,
#64/#65 bleiben offen und enthalten die zwingenden Folgeabnahmen. Read-back aller neun Issues über
MCP bestätigt Bodies, Taskmarker, Status, Requirements, Labels, textuelle Dependencies und unveränderte
manuelle Inhalte. Zusätzlich wurden sämtliche nativen Dependencies per lesendem GH-API-Aufruf geprüft.
Drei Updates, ein Abschluss, sechs unveränderte Issues; Soll-Ist-Wiederholung benötigt null Updates.
Mapping: docs/development/github-readiness-issue-sync.json. Kein Kommentar, PR oder Label angelegt.

Die geprüfte Gitintegration wird nach Push des Issuecommits und anschließendem regulären Merge
nach main anhand Remote-SHAs und Commit-Erreichbarkeit verifiziert. Gitnachweis folgt in
`docs/development/github-readiness-git-integrations.json`; keine Force-Pushes oder Branchlöschung.
