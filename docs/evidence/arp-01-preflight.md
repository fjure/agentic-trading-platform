# ARP-01 · Vorprüfung der Anbieterquellen

Stand 13.11.2026. Lokaler HEAD und das lesend geprüfte GitHub-`main` standen auf `768f56d29d017d1af7e41e3712afe749dc1fe806`. Der [Contracts-Lauf für diesen Commit](https://github.com/fjure/agentic-trading-platform/actions/runs/43403877983) war erfolgreich. Alle zehn Implementierungsissues waren offen. Die 16 eingehenden Abhängigkeiten entsprachen dem Issue-Graphen.

`./scripts/openspec.ps1 validate agent-research-production-readiness --strict` und `scripts/validate-contracts.py` bestanden auf der Basis. `scripts/check-contract-breaking.py --base-ref 768f56d29d017d1af7e41e3712afe749dc1fe806` meldete keinen Fehler und eine bestehende `allOf`-Warnung. Diese Ergebnisse beziehen sich auf die Ausgangsbasis.

## Offener Abnahmenachweis

- **OpenAI GPT-6 Luna.** Die Modellseite nennt 1.050.000 Kontexttokens. Die Responses-Referenz dokumentiert das Eingabelimit bei deaktivierter Trunkierung und die Aufschlüsselung der Usage. Der genaue Nachweis, dass die Grenze für den gebundenen strukturierten Request sämtliche abrechenbaren Eingaben einschließlich providerseitiger Zusätze erfasst, ist offen. Die Referenz weist außerdem darauf hin, dass der tatsächliche `service_tier` vom angeforderten abweichen kann. Die erreichbaren Abrechnungstarife für `service_tier=default` sind daher vor der Freigabe der Beispielreserve zu belegen. [Modell und Preise](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Responses-Referenz](https://developers.openai.com/api/reference/resources/responses/methods/create) · [Tokenzählung](https://developers.openai.com/api/docs/guides/token-counting)
- **Anthropic Claude Haiku 5.5.** Die Kontextdokumentation zählt alle drei Input-Usage-Komponenten zum Kontextlimit. Der bei Structured Outputs eingefügte Systemprompt wird wie anderer Systeminput abgerechnet. Der Preis von 0,625 USD je Million Inputtokens im langen Kontext deckt den Cache-Write mit fünf Minuten ab. Der Ein-Stunden-Tarif kostet 1 USD je Million und muss über die gebundene Requestkonfiguration ausgeschlossen sein. [Kontextgrenzen](https://platform.claude.com/docs/en/build-with-claude/context-windows) · [Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) · [Preise](https://platform.claude.com/docs/en/about-claude/pricing)

Der unabhängige Quellenreview bestätigte die offenen OpenAI-Nachweise. Der in ARP-01 geforderte vollständige Katalognachweis bleibt offen. Gemäß `ProviderSmokeBudgetPolicyV1` bleibt der Versand bei fehlender Schranke oder unvollständigem Tarif gesperrt. In dieser Vorprüfung erfolgte kein Providerrequest.

## Fortsetzung nach fachlicher Entscheidung

Die Betreiberentscheidung erlaubt unvollständig belegte Kandidaten in der technischen Plattformabnahme. Die Dispatch-Anforderungen gelten weiterhin für jede konkrete reale Bindung. Issue #96 wurde über GH CLI aktualisiert und zurückgelesen, nachdem der GitHub-MCP HTTP 403 gemeldet hatte.

- OpenSpec Strict, Schema- und Beispielprüfung sowie Redocly-Lint bestanden. Der semantische Diff zu `768f56d` meldete keinen Fehler und die bestehende `allOf`-Warnung.
- `./scripts/check-angular-client.ps1` bestätigte 491 bytegleiche Dateien und die TypeScript-Kompilierung. Der unabhängige Review korrigierte die Typmodellierung und den Hashbereich des Issue-Managedblocks.
- Die vollständige Contract-Suite mit den gezielten Setup-/Provider-Tests ergab nach Korrektur der Regressionen 2525 bestandene Tests und zehn unter Windows übersprungene POSIX-Setup-Tests. Die neuen ASGI-Fälle prüfen Authentifizierung, redigierte Kataloggründe und abgewiesene Probe und Aktivierung ohne Providerkomposition. Weitere 23 Gateway-/Relay-Tests bestanden. Der Quellcode-Secret-Scan meldete null Funde.
- `docker compose -p atl-arp96-proof -f infra/compose/compose.yaml --profile research-gateway up --build --wait --wait-timeout 45 research-llm-gateway` startete den isolierten Gateway. `GET /health` lieferte `DISABLED`. Dieser Lauf verwendete den Arbeitsstand auf Basis von `768f56d`. Danach wurden Container und Netzwerk des Prüfprojekts gestoppt und entfernt.

## Integrierte Prüfung der gesperrten Installation

- Die kombinierte Compose-Konfiguration mit den Profilen `lab`, `research-control` und `research-gateway` wurde mit `docker compose config --quiet` geprüft.
- Das neu gebaute Gateway-Image meldete `DISABLED`, wies den privaten Dispatch mit HTTP 503 ab und enthielt den versionierten Katalog.
- `python -m pytest tests/integration/test_companion_runtime.py -q` prüft die lokale Klärung mit der Control API, dem signierten Research-Relay über Loopback und frisch migriertem PostgreSQL. Der Operator ruft den gesperrten Status samt Sperrgründen ab und beantwortet eine lokale Rückfrage.

ARP-01 bleibt offen. Der Integrationstest verwendet temporäre Datenbankrollen und den Relay im Prüfcontainer. Der Start der vollständigen Compose-Installation mit den vorgesehenen Secret-Mounts und Diensteinstiegspunkten bleibt separat nachzuweisen. Die Remote-CI startet für den Issue-Branch gegenwärtig nur über einen Pull Request. Ein Push auf `main` würde die Prüfung erst nach der Integration auslösen.
