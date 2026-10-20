# Proposal

## Why

Nach der sichtbaren Fake-Demo soll der Nutzer ein selbst eröffnetes Alpaca-Paperkonto vollständig über die lokale Anwendung betreiben können. Die vorhandene Einrichtung speichert lediglich Schlüssel; Kontoprüfung, passende finanzielle Startbasis, Betriebswechsel und Orderbedienung benötigen heute private Operatorbefehle und sind noch nicht als zusammenhängender Dashboardablauf zertifiziert.

## What Changes

- Geführter Einstieg mit offizieller externer Alpaca-Kontoeröffnung, privater Schlüsseleingabe und anschließender Kontoprüfung durch Execution. Keine Broker-API-Kontoeröffnung, personenbezogenen Kontoanträge oder automatische Accountlöschung.
- Eigener dauerhaft gebundener Paperledger mit aus geprüftem Brokerbestand übernommener einmaliger Anfangsbasis; vorhandenes Fake-Lab und Demoresultate bleiben erhalten. Frei wählbares USD-Riskbudget ist von Broker-Cash getrennt.
- Authentifizierte Dashboardoperationen für Kontoprüfung, Vorbereitung, explizite Paperaktivierung, Pause/Stop/Kill, Strategiewahl und begrenzte manuelle LIMIT-Aufträge sowie einzelne Cancelanforderungen. Setup und Verbindungsprüfung erteilen keine Handelsfreigabe.
- Durchgehender normaler Strategiepfad mit unveränderlichen preislich begrenzten Vorschlägen, deterministischer Risiko-/Budgetprüfung und alleiniger Brokerausführung durch Execution. Bestehende Mock-/Baselinevarianten bleiben auswählbar; neutrale Entscheidungen erzwingen keinen Trade.
- Persistente Orders, Fills, Positionen, Kontostand, Reconciliation, Fehler und Tracebelege im klar markierten Alpaca-Paperkontext; sichere Wiederaufnahme nach Neustart mit geschlossenen Einstiegsgates.
- Vollständige Offlinezertifizierung mit realer lokaler Datenbank und Browser; tatsächliche Providerprüfung ausschließlich vom Menschen in seiner Anwendung.

## Capabilities

### New Capabilities

- `alpaca-paper-workflow`: zusammenhängende geführte Kontobindung, isolierter Paperledger, Betriebsfreigabe, Auftragslebenszyklus und sichtbarer Paperstatus.

### Modified Capabilities

Keine publizierten Main-Specs vorhanden (`openspec list --specs`: leer). Die abgeschlossenen, noch nicht synchronisierten Changes `paper-trading-vertical-slice` und `dashboard-trading-demo` bleiben verbindliche Grundlagen; dieser Change erweitert deren Verhalten additiv und übernimmt ihre Safetyinvarianten.

## Impact

Betroffen: kanonischer HTTP-Vertrag/Beispiele und generierter Angularclient; `services/control_api`, `services/runtime`, `services/execution`, `services/worker`; Brokerfähigkeit und Reconciliation in `packages/brokers`; Persistence/Migrationen, Risk/Budget, Compose und Launcher; Dashboard, Offline-PG-/Recovery-/Browserprüfungen und Runbooks. Keine neue Brokerbibliothek oder neue MCP-Berechtigung erforderlich; bestehende gepinnte Toolchain weiterverwenden. LIVE, Agentenorders, automatisches Kontoeröffnen, Geldtransfers, Kontolöschung und Broker-Cashreset bleiben ausgeschlossen. Nutzerbestätigung vom20.10.2026: Konto selbst eröffnen, danach Bedienung vollständig in der Anwendung.
