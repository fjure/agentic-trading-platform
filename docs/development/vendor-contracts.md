# Externe Verträge: Jev und Alpaca Paper

Abrufdatum: **2026-10-12**. Dokumentiert sind ausschließlich öffentlich beschriebene Verträge. Beispiele sind synthetische Vertragsfixtures, keine echten Anfragen. Es werden keine API-Schlüssel verwendet und keine Orders gesendet.

## Jev (TypeSafe)

Offizielle Quellen: [API-Referenz](https://docs.typesafe.ai/api), [Einführung](https://docs.typesafe.ai/introduction), [Noul](https://docs.typesafe.ai/primitives/noul), [Choice](https://docs.typesafe.ai/primitives/choice), [Score](https://docs.typesafe.ai/primitives/score). Endpunkt `POST https://api.typesafe.ai/v1/systemone`; Authentifizierung `Authorization: Bearer <API_KEY>`. Modellkennung `jev-latest` ist ein beweglicher Anbieter-Alias. Intern als `providerManaged` abbilden und die im Ergebnis zurückgegebene konkrete Modellkennung zusammen mit Anfrage/Antwort und Zeitstempel im Replay-Metadatensatz speichern. API-Schlüssel gehören ausschließlich in den serverseitigen Secret Store.

Anfragebeispiel:

```json
{
  "state": {
    "symbol": "SPY",
    "features": {"trend": "mixed", "volatility": "normal"}
  },
  "model": "jev-latest",
  "questions": {
    "trade_direction": {
      "type": "choice",
      "instructions": "Welche Aktion passt zu den bereitgestellten Merkmalen?",
      "criteria": {
        "abstain": "Keine Position eröffnen",
        "long": "Eine Long-Position vorschlagen"
      }
    },
    "setup_quality": {
      "type": "score",
      "instructions": "Wie gut ist das Setup anhand der Merkmale?",
      "criteria": ["schwach", "mittel", "stark"]
    },
    "data_complete": {
      "type": "noul",
      "instructions": "Sind die bereitgestellten Merkmale vollständig?"
    }
  }
}
```

Antwortfixture nach dokumentierter Struktur:

```json
{
  "model": "jev-1.x",
  "answers": {
    "trade_direction": {
      "type": "choice",
      "choice": "abstain",
      "probabilities": {"abstain": 0.82, "long": 0.18},
      "confidence": 0.72
    },
    "setup_quality": {
      "type": "score",
      "score": 0.35,
      "legend": {"0": "schwach", "1": "mittel", "2": "stark"},
      "probabilities": {"0": 0.7, "1": 0.25, "2": 0.05},
      "confidence": 0.61
    },
    "data_complete": {"type": "noul", "noul": 0.93}
  },
  "usage": {"input_tokens": 120, "output_tokens": 35}
}
```

Die konkrete Modellkennung und Zahlen in diesem Fixture sind Beispiele. Noul ist eine Zahl von 0 bis 1. Choice braucht eine Kriterien-Zuordnung; Score braucht eine geordnete Kriterienliste mit 2 bis 10 Stufen. Choice liefert je Option eine Wahrscheinlichkeit und einen ausgewählten Schlüssel; Score liefert einen wahrscheinlichkeitsgewichteten Zahlenwert, Legende, Stufenwahrscheinlichkeiten und `confidence`. `confidence` ist kein Gewinn- oder Erfolgsmaß. Die offizielle Referenz dokumentiert HTTP `401`, `422`, `429` und `529`; bei `429`/`529` exponentiell mit Zufallsanteil verzögern. Nur bei eindeutig lesbaren/replaybaren Requests erneut senden. Ein timeout nach Anfrageannahme gilt als unbekannter Ausgang, bis ein Anbieter-Request-Identifier oder eine andere belastbare Zuordnung verfügbar ist; keine automatische Handelsfreigabe daraus ableiten.

**Lokaler Vertrag:** Beispielgestützte, versionierte interne Modelle dürfen exakt die oben dokumentierten Pflichtfelder und Union-Typen festhalten. Zusätzliche Felder tolerieren, fehlende Pflichtfelder, unbekannte Antworttypen, ungültige Wahrscheinlichkeiten oder nicht zu den Frage-IDs passende Antworten ablehnen. Das Fehlen einer herunterladbaren Anbieter-OpenAPI-/JSON-Schema-Datei ist kein Blocker: Quelle für den lokalen Vertrag ist die offizielle Feldreferenz. Vor produktiver Adapterzertifizierung bleiben echte, autorisierte Anbieterantworten und Fehlerfälle als spätere Evidenz erforderlich.

## Alpaca Paper: Capability-Matrix

Offizielle Quellen: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1), [Create Order](https://docs.alpaca.markets/us/reference/postorder), [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading), [Order-Verhalten](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [Crypto Orders](https://docs.alpaca.markets/us/docs/crypto-orders), [Crypto Trading](https://docs.alpaca.markets/us/docs/crypto-trading-1), [Market Sessions](https://docs.alpaca.markets/us/docs/245-trading-for-trading-api), [Order-Events](https://docs.alpaca.markets/us/docs/websocket-streaming), [Activities](https://docs.alpaca.markets/us/reference/getaccountactivities-2), [Rate Limits](https://docs.alpaca.markets/us/docs/broker-api-rate-limits).

| Fähigkeit | US-Aktien / ETFs (SPY, QQQ) | Crypto (BTC/USD) | Einschränkung für den Adapter |
|---|---|---|---|
| Papier-Endpunkt | `https://paper-api.alpaca.markets/v2` | derselbe Trading-Paper-Endpunkt | Nur Paper-URL und Paper-Credentials; ein falscher Host blockiert fail-closed. Paper ist eine Simulation, ohne Marktimpact, Latenz-Slippage oder Queue-Position; kann auch unrealistische große Liquiditätsfills und zufällige Teilfills simulieren. |
| Handelbarkeit/Symbol | API `/v2/assets?status=active&asset_class=us_equity`; beim Start zusätzlich `tradable`, Status und Eigenschaften prüfen | API liefert Asset-/Paarliste; aktuelle Schreibweise `BTC/USD` (Slash URL-encodieren, wo es Teil des Pfads ist) | SPY/QQQ sind gewünschte Kandidaten, keine Zusicherung zu Kontorechten oder momentaner Aktivität. Keine statischen Fähigkeitsannahmen: Symbol und Asset-ID aus Account-Endpunkt-Lookup übernehmen und unveränderlich in der Order-Evidenz speichern. |
| Marktzeiten | Regulär 09:30–16:00 US/Eastern an Börsentagen; erweiterte Sessions/24/5 existieren und folgen Feiertags-/Asset-Regeln | 24 Stunden täglich, 7 Tage | Baseline nur reguläre US-Aktien-Session; Markt-Kalender/Clock in US/Eastern vom Anbieter abfragen, Feiertage/verkürzte Tage beachten. Crypto jederzeit, aber nur nach bestätigter Account-/Region-Berechtigung. |
| Ordertypen / TIF | Markt und Limit sind nötig; Anbieter unterstützt darüber hinaus Stop/Stop-Limit/Trailing. Regulär `day`/`gtc` usw. nach Ordertyp | Markt, Limit, Stop-Limit; `gtc` und `ioc` | Baseline begrenzt auf Markt/Limit; TIF je Ordertyp validieren. Extended-hours nur Limitorders mit zulässigem TIF; hier deaktiviert. |
| Bracket/OCO/OTO | Anbieter unterstützt Equity `bracket`, `oco`, `oto`; Bracket-Ausgänge können in volatilen Bedingungen beide füllen | nicht unterstützt; nur `simple` | Für erste Integration deaktiviert. Nicht aus Anbieterfähigkeit als Domänenfreigabe folgern. Vor separater Freigabe FakeBroker-Rennen/Teilfill/Reconciliation abdecken. |
| Fractional | Asset muss `fractionable=true`; `qty` und `notional` gegenseitig exklusiv. Fractional/Notional ist auf kompatible Order-/TIF-Kombinationen beschränkt; insbesondere Notional nur Markt und `day`, Fractional-Menge Markt/Day; Fractional nicht shortbar. | Bruchteile unterstützt; Asset liefert Mindestmenge/Inkremente (Beispiel 0.0001, nicht universell) | Dezimalstrings verwenden. Laufzeit-Eigenschaften, Mindestgröße, Schrittweite und Preisinkrement validieren. Nie `float`; Rundung gegen Asset-Info. |
| Leerverkauf | Plattform unterstützt Short nur mit geeigneter Margin-/Borrow-Berechtigung und `easy_to_borrow`; Konto muss zulässig sein | Nicht marginierbar und nicht shortbar | Projektbaseline: **keine Short-Orders** für beide Klassen. Positions- und Asset-Prüfung erzwingt nur `buy` zum Öffnen sowie Verkauf höchstens bis zur bestätigten Long-Menge. |
| Mindestgrößen/Fees | Preis-/Mengenregeln aus Asset und Konto; Gebühren/Regulatorik nicht pauschal null annehmen | Paarabhängige Mindestmenge, Inkrement und Preisinkrement; Crypto-Gebühren gelten, dokumentierte Order-Notionalgrenze 200.000 USD | Vor jeder Order Metadaten laden, Kosten konservativ einbeziehen. Eine fehlende/alte Capability-Antwort führt zur Ablehnung. |
| Idempotenz / Status | `client_order_id` vom Client; Abruf `/v2/orders:by_client_order_id`; Events über `trade_updates` | gleiches Ordermodell | ID vor Senden persistieren; bei Timeout nicht erneut posten, sondern per Client-ID nachschlagen und offene Orders/Events abgleichen. `order.id` (UUID), `client_order_id`, `execution_id`, Zeitstempel und Fills speichern. |
| Fills / Reconciliation | `trade_updates` enthält `new`, `partial_fill`, `fill`, `canceled`, `expired`, Ersetzungen/Ablehnungen. Activities mit `order_id` und Pagination belegen tatsächliche Ausführungen. | gleiches Status-/Activity-Prinzip, crypto-spezifische Mengen und Gebühren | Stream ist schnell, REST-Abgleich ist maßgeblich nach Neustart/Disconnect. `/v2/orders` mit Status `all`, Zeitfenster/IDs und verschachtelten Legs abrufen; Activities nach `order_id` abfragen und kumulierte Menge/Wert gegen Order und Position abstimmen. Unbekannter Zustand bleibt `UNKNOWN`, nicht als Fill oder Cancel interpretieren. |
| Rate limit | Header-/Planlimits nicht als unveränderliche Konstante annehmen; HTTP `429` kann vorkommen | ebenso | Limit-/Remaining-/Reset-Header lesen, Backoff mit Jitter. Bei schreibenden POSTs niemals blind wiederholen; nach 429 oder Verbindungsabbruch Order-ID-Lookup und Reconciliation vor neuem Versuch. |

### Vertragsanwendung

`/v2/assets` ist Quelle für handelbare Assets und Felder wie `tradable`, `fractionable`, `shortable`, Mindestgröße und Inkremente. Die Laufzeitfähigkeit muss zu jedem Symbol neu bestimmt werden. Die API-Metadaten ersetzen weder deterministische Risikoprüfung noch zusätzliche Orderfreigabe. Das Projekt lässt LIVE technisch unerreichbar und sendet in diesem Dokument keinerlei Requests.

Paper-Ausführungen sind keine Belege für echte Ausführungsqualität. Vor allem keine Reconciliation nur auf WebSocket-Events stützen: Event-Strom kann nach Verbindungsverlust Lücken haben; Order- und Activity-Endpunkte erneut lesen, Dubletten anhand stabiler IDs beseitigen und Mengen/Werte dezimal abstimmen. Unbekannte Statuswerte lösen einen Halt und manuelle Prüfung aus.
