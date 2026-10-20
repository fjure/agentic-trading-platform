# Quellen und beobachtete Grundlagen

Geprüft am20.10.2026 über Context7 `/websites/alpaca_markets_us` (High reputation) und direkte offizielle Seiten. Context7-Suche liefert teilweise Broker-/Local-Currency-Beispiele; diese werden nicht als US-Trading-API-Freigabe übernommen. Bestehenden direkten HTTPadapter und gepinnte Projektversionen weiterverwenden; keine neue SDKversion behauptet.

| Quelle | Konsequenz für diesen Change |
|---|---|
| [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading) | Persönliche Paperkontoeröffnung extern; separate Paperkeys, fester Paperhost, Paper-only erhält IEX. NBBO-Simulation ist keine echte Ausführung. Seite nennt sowohl ältere Resetformulierung als auch neue Create/Delete-UI; Anwendung bietet keinen Reset oder Accountdelete an. |
| [Getting Started Trading API](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api) | Persönliches Konto und erzeugte Schlüssel als Voraussetzung; bestehendes Tradingkonto anbinden. |
| [Getting Started Broker API](https://docs.alpaca.markets/us/docs/getting-started-with-broker-api) | Automatische Eröffnung ist separates Brokerprodukt und nach Nutzerklärung ausdrücklich nicht Teil dieses Changes. |
| [Get Account](https://docs.alpaca.markets/us/reference/getaccount-1) | Paper `GET /v2/account`; erwartete UUID, USD/Status/Sperren und echte Bestände prüfen; Kontonummer ist nicht UUID. |
| [Create Order](https://docs.alpaca.markets/us/reference/postorder) | Dauerhafte Client-ID, LIMIT-Preis und DAY; qty/notional exklusiv. Referenz nennt Fractionals teilweise weiterhin nur MARKET: Widerspruch zur Matrix dokumentiert, kein stiller Capabilityfallback. |
| [Orders at Alpaca](https://docs.alpaca.markets/us/docs/orders-at-alpaca) | Über Context7 gefundene Fractional-USD-Matrix nennt DAY-LIMIT; negative Fähigkeiten weiter strikt begrenzen. |
| [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading) | Fractionable-Assetprüfung, bis9 Dezimalstellen, dokumentierte DAY-LIMIT-Unterstützung; ältere widersprüchliche Schlussnotiz verhindert pauschale Ausweitung ohne Zertifizierung. |
| [Working with orders](https://docs.alpaca.markets/us/docs/working-with-orders) | Submit-/Status-/Client-ID-Verfolgung; Beispiele unterschiedlicher Assetklassen nicht als Freigabe neuer Klassen übernehmen. |
| [Real-time Stock Data](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data) | Feed/Entitlement explizit, tatsächliche Quotes/Bars erforderlich; kein unberechtigter SIPzugriff oder erfundener Spread. |
| [Websocket trade updates](https://docs.alpaca.markets/us/docs/websocket-streaming) | Teilfill/Fill/Cancel sind getrennte Ereignisse; REST/Activities bleiben für initiale Recovery ausreichend, Stream nicht als alleiniger Vollständigkeitsbeweis. |

Bestandsanalyse durch Hauptagent und separaten read-only Subagent: `services/runtime/{config,execution,worker,provision}.py`, `services/worker/{planner,paper_smoke_plan}.py`, `services/execution/{broker_binding,paper_reconciliation,broker_recovery,paper_smoke}.py`, `packages/brokers/alpaca_paper.py`, `docs/runbooks/{alpaca-setup,paper-smoke,dashboard-demo}.md`. Keine realen Credentials oder Providerrequests. Details und Entscheidungen in `design.md`.
