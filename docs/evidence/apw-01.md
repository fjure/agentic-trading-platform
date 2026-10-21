# APW-01: Workflowverträge und fachliche Orderquellen

Stand: 20.10.2026. Umfang: REQ-APW-001/002/005/006/007/010, ausschließlich Vertragsbasis und additive Domainwerte. Runtimeaktivierung, Providerprüfung, Persistenz neuer Quellen und Risk-/Executionintegration werden erst in den nachfolgenden Tasks zertifiziert.

## Implementierung

Acht additive Operationen unter `/api/v1/paper/`: Status, einzelner Vorgang, Verbindungsprüfung, einmalige Vorbereitung, Start, Kontrolle, menschliche LIMIT-Anforderung und einzelne Cancelanforderung. Schreiboperationen verlangen Idempotency-Key und liefern einen dauerhaften Vorgang; Annahme bedeutet keinen Erfolg. PAUSE/KILL verlangen eine dauerhaft geschlossene Einstiegslatch vor Bestätigung, unabhängig von laufender Provider-I/O. Keine freie Providerantwort oder Credentialfelder in öffentlichen DTOs. Kontoidentität erscheint ausschließlich redigiert.

Start und LIMIT-Anforderungen binden Konto-/Credentialgeneration, Policy-/Profil-/LIMIT-Policy-/Budgetversion, Ablaufzeit und Paperbestätigung. Ausschließlich USD, SPY/QQQ, LIMIT/DAY/REGULAR. Vier eigenständige versionierte JSON-Schemas mit positiven und negativen Beispielen ergänzen den unveränderten bisherigen Vertrag.

`packages/domain/paper_workflow.py` definiert immutable menschliche Anforderungen und Paperproposals. HUMAN_REQUEST verlangt die ursprüngliche menschliche Anforderung, MODEL_DECISION eine echte Modellentscheidung; die jeweils andere Referenz ist ausgeschlossen. Referenzprüfungen binden unveränderliche fachliche Werte und Trace, erlauben nur kürzere TTLs und verwenden exakte Decimalarithmetik. Der bisherige reflektierte SQL-Domaintyp bleibt unverändert. APW-07 muss die neue Quelle dauerhaft speichern und an den bestehenden gemeinsamen Risk-/Executionpfad anbinden; dieses Dokument behauptet dafür keine Laufzeitfreigabe.

## Prüfungen

- `scripts/validate-contracts.py`: bestanden, 39 HTTP-Operationen, 18 Schemas, 17 primäre Schemabeispiele und 21 OpenAPI-Antwortbeispiele.
- `scripts/check-contract-breaking.py --base-ref f2afbfc9f56675f8dc74fce9988c35acb5ba5928`: oasdiff1.33.0 bestanden, tatsächlicher vorangehender main-Commit; vorhandene Operationen und Komponenten zusätzlich strukturell unverändert.
- Unabhängiger begrenzter Contract-/Safetyreview einschließlich finaler Generator-Kompatibilitätsänderungen ohne blockierenden Befund; 127 gezielte Domain-/Vertragsprüfungen bestanden.
- Redocly2.62.1: bestanden ohne Warnungen. Zwei bytegleiche Generatorläufe mit104Dateien und konkrete Decision-/Paperstatus-/Vorgangs-/Human-Proposal-Typfixtures bestanden. Ausschließlich mechanische Generierung, keine Clientpatches. Direkte Conditionals erhalten nullable Typen; doppelte Negation erhält Array-Eindeutigkeit ohne falsche Set-Abbildung. Negativtests prüfen weiterhin doppelte Symbole/Blocker.
- Angular-Produktionsbuild bestanden; Größenwarnungen für bestehendes Bundle und Supervisor-CSS bleiben sichtbar.
- Vollständige finale Vertragsmatrix: 502bestanden, zwei fehlgeschlagen (28,49s), identisch zum vorangehenden Lauf. Die beiden exakten Route-/Vertragsabgleiche erwarten die neuen acht Paper-Routen bereits in `create_app`; laut Aufgabenplan werden sie erst in APW-03/05/07/08 implementiert. Kein Test wurde gelockert, kein Laufzeitstub ergänzt. Der eingeschränkte erste Lauf hing und wurde beendet; die vollständigen ASGI-Läufe nutzten den freigegebenen lokalen Prozesszugriff.
- Vorbereitender vollständiger Issue-Read-back über den autorisierten GH-CLI-Fallback: 0Änderungen, zehn unveränderte offene APW-Issues. APW-01 bleibt wegen des Integrationsblockers offen, kein Commit/Push/Merge behauptet.

## Historischer Ablaufblocker20.10.2026

Der Plan verlangt bestandene APW-01-Gates vor nachfolgenden Implementierungen und einzelne geprüfte Issueintegrationen nach main. Die bestehende vollständige Route-/Vertragsgleichheit verbietet gleichzeitig einen ausschließlich kanonischen Vertrags-Zwischenstand. Nutzerentscheidung20.10.2026 ausdrücklich erteilt: eigene Issuebranches erhalten, APW-01 bis zur tatsächlichen Implementierung der abhängigen Routen offen lassen und main erst nach gemeinsamer Vertragsabnahme integrieren. Abhängige Implementierungen sind damit freigegeben; die zwei vollständigen Vertragsprüfungen bleiben unverändert und verpflichtend. APW-01 bleibt bis dahin offen.

Branch: `codex/ptv-32-paper-workflow-contracts`, Basis `f2afbfc9f56675f8dc74fce9988c35acb5ba5928`. Vorhandene Nutzeränderung `apps/dashboard/angular.json` (Analytics aus) bleibt außerhalb des Pakets.

## Safetygrenze

Der Stand definiert ausschließlich Verträge und Validierung. Kein Brokerport im Browser/API, keine neuen Runtimegates, kein LIVE und keine Providerorders. Agenten verwendeten ausschließlich synthetische Werte und Offlineprüfungen. Tatsächliche Alpaca-Fähigkeiten und private menschliche Providerabnahme bleiben unverifiziert.

## Gemeinsame Vertragsabnahme21.10.2026

Die tatsächlichen Paper-HTTP-/Kommandoadapter sind im laufenden APW-03 vorhanden. Unveränderte vollständige Routendeckungsprüfungen bestehen jetzt; gesamte Vertrags-/Human-Domainmatrix629passed/7,94s. Keine Stubs, keine Ausnahmen in den beiden vorher fehlgeschlagenen Tests. Vertragsvalidator, positive/negative Beispiele, vorhandene additive Baseline-/Client-/Buildnachweise und unabhängige Reviews gelten weiterhin. Aktualisierte Generator-/Formatprüfstrategie in `docs/development/contract-validation.md`. Damit ist APW-01 fachlich lokal abgeschlossen; APW-03 und die vollständige Handelsabnahme bleiben getrennt offen. Die lokalen Issuebranches werden erst nach der freigegebenen gemeinsamen finalen Abnahme nach main integriert.

Issue#32 geschlossen und alle zehn APW-Issues vollständig zurückgelesen. Idempotente Wiederholung:0Updates/10unverändert. APW-01 und APW-02 abgeschlossen, OpenSpec2/10. Die früheren offenen Zustände oben dokumentieren den damaligen Zwischenstand.
