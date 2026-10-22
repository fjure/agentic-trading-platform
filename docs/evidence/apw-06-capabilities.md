# APW-06: lokale Zertifizierung von Fractional DAY-LIMIT

Stand: 2026-10-22. Grundlage: ADR-APW-004 im OpenSpec-Design. Diese Evidenz zertifiziert die lokale Abbildung gegen einen HTTP-Double. **Es wurde keine Order an Alpaca gesendet. Die tatsächliche Providerfähigkeit ist weiterhin unverifiziert.**

## Quellen und dokumentierter Widerspruch

Context7 wurde zuerst über `resolve-library-id` für die Alpaca Trading API angefragt. Die Antwort meldete ein ausgeschöpftes Monatskontingent. Anschließend wurden ausschließlich die offiziellen Alpaca-Seiten gelesen:

- [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading): beschreibt Fractional-LIMIT mit DAY, höchstens neun Dezimalstellen und die ausdrückliche Prüfung von `fractionable=true`. Gleichzeitig übermittelte Mengen- und Notionalfelder werden dort ausgeschlossen.
- [Create an Order](https://docs.alpaca.markets/us/reference/postorder): der Beschreibungstext zu `qty` beschränkt Fractional weiterhin auf MARKET und DAY. Diese Einschränkung widerspricht der allgemeinen Fractional-Beschreibung. Sie wird hier ausdrücklich nicht als durch einen echten Provider-Test aufgelöst dargestellt.
- [Orders at Alpaca](https://docs.alpaca.markets/us/docs/orders-at-alpaca): erläutert reguläre und erweiterte Sessions sowie Ordertypen. Der lokale Umfang bleibt enger als die dokumentierten erweiterten Handelszeiten.

ADR-APW-004 erlaubt die konkrete, lokal zertifizierte Kombination trotz dieses Quellenkonflikts. Daraus folgt keine Freigabe weiterer Ordertypen, Sessions, Assets oder eines MARKET-Fallbacks. Eine spätere echte Paper-Abnahme bleibt eine ausdrücklich menschliche Handlung.

## Zertifizierte Grenze

| Eigenschaft | Lokale Zusicherung |
| --- | --- |
| Instrumente | SPY und QQQ; aktives, handelbares US-Equity-Asset |
| Fractional-Freigabe | Frisch gelesener boolescher Wert `fractionable is True`; andere Werte reichen nicht |
| Order | BUY oder gedeckter SELL, LIMIT, DAY, einfache Order, `extended_hours=false` |
| Menge | Positiver Decimal-Wert auf dem 0.000000001-Raster; keine Aufrundung im Adapter |
| Preis | Unverändertes Cent-Raster ab 1 USD, darunter 0.0001 USD; unveränderte Cashpuffer-/Bestandsprüfungen |
| Payload | Exakte Dezimalstrings, ausschließlich `qty`; kein zusätzliches `notional` |
| Frische | Erneute Konto-, Asset-, Clock- und Kalenderprüfung vor dem einzigen POST; bestehende Frist unter 120 Sekunden und reguläre Session bleiben bestehen |
| Fehler | Providerablehnung oder ungewisser POST-Ausgang bleiben konservativ UNKNOWN; Abgleich per GET statt erneutem POST |

`capabilities(symbol)` und `binding_proof(symbol)` liefern frische `BrokerCapabilities`. Bei bestätigtem Fractional-Asset sind `fractional`, `limit_order` und `regular_session` CONFIRMED; `quantity_step` ist 0.000000001. Der generische `price_tick` ist 0.0001; die zusätzliche Cent-Regel wird weiterhin beim Submit geprüft. CONFIRMED bezeichnet diese lokale Kombination, keine erfolgreiche echte Providerabnahme.

Die Capability-Version enthält jetzt den internen Token `equity-day-limit-fractional.offline-v1` zusammen mit Konto-/Assetidentität, Fractionalwert, Rastern, Session und Cashpuffer. Alte Capability-Versionen werden damit nicht still für die erweiterte Kombination wiederverwendet. Ein Assetwechsel während der Vorbereitung verhindert den POST. Ganze Equitymengen und Crypto behalten ihre bisherigen Prüfungen.

## Prüfungen

Neue Datei: `tests/contracts/test_alpaca_fractional_limit.py` mit 32 Fällen. Der lokale Double prüft den festen Paperhost. Abgedeckt sind BUY/SELL für beide Symbole mit einer, neun beziehungsweise gemischten Dezimalmengen; exakte DAY-LIMIT-Payloads; ungültige oder wechselnde Assets; fehlende Fractional-Bestätigung; zehn wirksame Nachkommastellen; Tickverletzungen; geschlossene Session und alte Clock; abweichende TIF-/Session-/Ordertyp-Antworten; HTTP 422 und Timeout mit weiterhin UNKNOWN und höchstens einem POST, auch nach Wiederherstellung des unveränderlichen Intents. MARKET-BUY bleibt gesperrt.

Der bestehende Contracttest ersetzt ausschließlich seine überholte Fractional-LIMIT-Ablehnungsannahme durch eine Mengenpräzisionsverletzung. Die MARKET-BUY-, Cash- und Short-Sperren bleiben erhalten.

Ausgeführt:

```text
.venv/Scripts/python.exe -m pytest tests/contracts/test_alpaca_fractional_limit.py tests/contracts/test_alpaca_paper.py tests/contracts/test_fake_broker.py tests/contracts/test_alpaca_activities.py tests/contracts/test_alpaca_initial_history.py tests/recovery/test_alpaca_fixtures.py -q --basetemp=.tooling/output/pytest-fractional-final-20261022
131 passed in 1.40s

.venv/Scripts/python.exe -m ruff check packages/brokers/alpaca_paper.py tests/contracts/test_alpaca_fractional_limit.py
All checks passed!
```

Die Änderung erteilt keine neuen Reservations-, Reconciliation- oder Datenbankrechte. UNKNOWN ist kein Beleg für Ablehnung oder Freigabe einer Reservation. Die dauerhafte wirtschaftliche Auflösung bleibt beim bestehenden Execution-/Reconciliationpfad. Diese Offlineprüfung ersetzt weder dessen integrierte SQL-Abnahme noch einen späteren menschlichen Provider-Test.
