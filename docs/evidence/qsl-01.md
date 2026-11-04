# QSL-01 / #77: additive Quant-Researchverträge

Apply am 30.10.2026 ausdrücklich freigegeben, nach Nutzungslimit am 04.11.2026
fortgesetzt. Ausgangsbasis und erneut verifizierter Remote-main:
`79931cf2f32799027da7de83049fd54be0e1e72e`.
Issuebranch: `codex/ptv-77-quant-lab-contracts`.

## Ergebnis und Abnahmegrenze

Vertragsimplementierung und Paketprüfungen abgeschlossen; Gitintegration und
Issueabschluss werden anschließend verifiziert. Die Checkbox bleibt bis dahin
offen. QSL-02 bis QSL-09 sind nicht durch dieses Paket implementiert.

14 additive Operationen auf zwölf neuen Pfaden, 75 geschlossene QSL-Schemas,
ein Standalone-Mirror mit 86 Definitionen und 53 typisierte öffentliche
Fixtures plus eine eigenständige Neunergrid-Draftfixture. REQ-QSR-001,
REQ-QEX-001..005/007..009 und REQ-QCO-001..005 sind im Vertragsumfang abgebildet:
versionierter Katalog, immutable Draft-/Validierungs-/Startbindungen,
geschlossene Policies mit explizit nullbaren Aktivitätsminima,
Nichtwahl und Nichtauswertung, exakte selectionValues neben Anzeigewerten,
Forschungsrollen/NOT_TRAINABLE, Aktivität/Exposure sowie paginierte
rollenübergreifende Wiederverwendung.

Die neuen Routen tragen bis QSL-07 ausdrücklich `x-runtime-available: false`.
Keine Runtime-Stubs. Die enge Abnahmestufung erlischt beim Abschluss von QSL-07
und scheitert bei fehlender oder mehrdeutiger Taskzuordnung. Alle Bestandsrouten
bleiben vollständig prüfpflichtig.

## Tatsächlich ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| `scripts/validate-contracts.py` am finalen Stand | PASS: OAS 3.1.2, 80 Operationen, 28 Schemadokumente, 27 Schema-Beispiele, 264 OAS-Responsebeispiele; zusätzlich 53 QSL-, 36 Readiness-, 16 retrospektive und 7 Offlinebundlefixtures |
| `scripts/check-contract-breaking.py --base-ref 379ebff` | PASS, oasdiff 1.33.0: 0 Errors, eine strukturelle AllOf-Warnung in unveränderter alter Readinessantwort |
| Redocly 2.62.1 mit vorhandener Konfiguration | PASS ohne neue Warnungen, keine abgeschwächten Regeln |
| `scripts/check-angular-client.ps1 -WriteClient`, anschließend ohne WriteClient | PASS: zwei Generierungen über jeweils 364 Dateien SHA256-gleich; Appclient identisch; TypeScript mit sämtlichen alten/neuen JSONfixtures und sechs negativen Typfixtures bestanden |
| `pytest tests/contracts -q -p no:hypothesispytest --tb=short` final | **2291 passed**, 63.96s, eine bestehende Starlette-TestClient-Deprecationwarnung |
| unabhängige QSL-Vertragsmatrix | **389 passed**, 53 Fixtures, Ruff PASS; auch Bestandteil der Gesamtsuite |
| unabhängiger Nullable-Review | 30 gezielte Tests bestanden; alle 67 nullable Alternativen disjunkt, Status-/Nullnegative erhalten |
| `pytest tests/unit -q -p no:hypothesispytest --tb=short` vor ausschließlich nachfolgenden Vertragsänderungen | **1231 passed, 1 skipped**, 10.79s; Skip ist bestehender Linux-tmpfs-Test, eine bestehende Deprecationwarnung |
| gezielte Legacy-/SMA-/Simulator-/Metrik-/Hashregression | 53 passed; auch Bestandteil der Unitsuite |
| Abnahmestufung | 12 passed; Bestandteil der Vertragssuite |
| Angular 22.2.2 Produktionbuild, gepinnter Node 22.23.3 | PASS, 9.335s; initial 966.02kB bei Warnschwelle 500kB, unverändertes Supervisor-CSS 7.12kB bei 4kB; keine Budgeterhöhung |
| Ruff auf Stage-/QSL-Testdateien | PASS |
| bestehender validate-contracts.py: Ruff-Vergleich gegen HEAD | identische 26 vorbestehende Codes/Meldungen; keine globale Lintbereinigung |
| Source-Credentialscan | 1417 Dateien, 0 Befunde, keine Werte ausgegeben |
| OpenSpec strict und git diff --check | PASS |
| bisheriger Issue-Sync samt Vorwärts-/Rückwärtsgraph | neun unveränderte offene Issues, keine Duplikate, 18 exakte native Kanten; abschließender Integrations-/Statusabgleich folgt |

Alle 59 bestehenden Pfade und 251 bestehenden Schemas sind AST-identisch zur
Basis. Nach Entfernen der zwei additiven Textfragmente ist der gesamte alte
Vertrag zusätzlich bytegleich. Bei der Sichtprüfung `git diff --histogram`
verwenden: der normale Diff ordnet wiederholte YAMLzeilen irreführend zu.

Finaler OAS-SHA256:
`cbb47b770c003f49140afbfa1f7fa34db2e045b2d8e323b7711f4a7703840658`.
Standalone-SHA256:
`dcfe07a6a8b352b4b884fd8144d117db88b6f4b3da94f5bc8b2a956f8c94458b`.

## Unabhängiger Review und behobene Befunde

Getrennte Contract-, Test- und read-only Reviewagenten; Hauptagent integriert.
Behoben und gezielt nachgeprüft: widersprüchliche Rollen/Folds, fehlende
Selektoridentität in publizierten Ressourcen, Erfolg ohne vollständigen
Publikationsbeleg, Exposition ohne frühere Quell-ID, nicht erreichbare
Historyfolgesseiten vor Start, fehlende exakte Auswahlwerte, künstlich
umetikettierte vollständige Kandidaten bei Teilfehlern, unvollständige oder
doppelte Gate-Evidenz und unzulässige Medianbildung über Integercounts.
Finaler unabhängiger Vertrags-/Safetyreview und Nullable-Nachreview melden
keine offenen P1/P2. Laufzeitisolation wird dadurch nicht zertifiziert.

Zwischenprüfungen fanden YAMLaliases oberhalb des Generatorlimits und
anschließend TypeScript-Nullfehler trotz bytegleicher Generierung. Additive
Fragmente werden nun ohne Aliases geschrieben. Disjunkte `oneOf`-Alternativen,
doppelte Negation für unveränderte Konditionen und ein äquivalentes null-only
Rankingfeld erhalten exakt dieselben zulässigen Instanzen. Keine manuellen
Clientänderungen, Fixturecasts oder abgeschwächten Typprüfungen. Die alten
Anfangsfehler und ein während paralleler Bearbeitung erkannter Generatordrift
sind durch die finalen vollständigen Läufe ersetzt, nicht als Erfolg gezählt.

Context7 war am 30.10. erschöpft; am 04.11. erfolgreich
`/openapitools/openapi-generator` abgefragt. Primärquellen ModelUtils.java und
typescript-angular/modelGeneric.mustache erklären die nullable-Erkennung.
Zusätzlich die [offizielle Generator-Dokumentation](https://openapi-generator.tech/docs/generators/typescript-angular/)
geprüft. Keine Toolversion oder Generatorvalidierung verändert.

## Grenzen

Keine QSL-Runtime, SQLmigration, Worker-, Browser-, Container- oder Lastabnahme
mit diesem Vertragsvorlauf behauptet. Kein Dispatch aktiviert, keine
Broker-/Provider-/Jevfähigkeit, kein Finanzrecht und keine Promotion ergänzt.
Reale Datenrechte, historische PIT-Eignung und Profitabilität bleiben
unverifiziert. Fremde Änderungen an README.md, apps/dashboard/angular.json
und vorbestehende temporäre .tooling-Verzeichnisse bleiben außerhalb des
Issuepakets. Keine Archivierung oder Main-Spec-Konsolidierung.
