# Alpaca-Marktdaten im Execution-Gateway

Stand und Quellenabruf: 2026-10-14. PTV-16 implementiert den read-only Adapter und die normalisierte Workerübergabe; Paper-Ausführung folgt PTV-17. Kein Broker-/Order-MCP, keine Orderoperation und kein externer Accounttest gehören zu diesem Nachweis.

## Offizieller Vertrag

Die Trading-API bietet [Account](https://docs.alpaca.markets/us/docs/account-plans), [Assets](https://docs.alpaca.markets/us/docs/working-with-assets), [Clock](https://docs.alpaca.markets/us/reference/legacyclock) und [Calendar](https://docs.alpaca.markets/us/reference/legacycalendar) über `/v2` am festen Paperhost. Der Kalender liefert Handelstage mit tatsächlichen Öffnungs-/Schlusszeiten einschließlich verkürzter Tage. Zeitangaben werden mit America/New_York in UTC überführt; verlängerte oder Overnight-Sitzungen sind nicht die reguläre Sitzung.

[Stockbars](https://docs.alpaca.markets/us/reference/stockbars) stammen vom Datenhost `/v2/stocks/bars`, [Cryptobars](https://docs.alpaca.markets/us/reference/cryptobars-1) vom versionierten `/v1beta3/crypto/us/bars`. Der Adapter fragt ausdrücklich 15Min und den gewählten Aktienfeed ab. [IEX und SIP](https://docs.alpaca.markets/us/docs/market-data-faq) sind unterschiedliche Datenquellen; erfolgreiche historische SIP-Abfragen können auch verzögert erlaubt sein und zertifizieren keinen Echtzeitzugang. Kein stiller Feedwechsel nach 403. Pagination muss vollständig oder als unvollständig abgewiesen sein.

[BTC/USD](https://docs.alpaca.markets/us/docs/crypto-trading) ist das normalisierte Paar; öffentliche Kryptodaten belegen weder Kontoaktivierung noch Handelbarkeit. Das Projekt prüft zusätzlich aktives, nicht gesperrtes USD-Konto, Cryptoaktivierung und aktives handelbares Asset. Die gefilterte Kryptoliste vermeidet die unterschiedliche alte Symbolschreibweise des einzelnen Assetlookup-Endpunkts. Für SPY/QQQ liest der Adapter ausschließlich das [einzelne Asset](https://docs.alpaca.markets/us/reference/get-v2-assets-symbol_or_asset_id), damit die gesamte Aktienliste den begrenzten Antwortpuffer nicht belegt. BTC/USD bleibt standardmäßig deaktiviert. SPY und QQQ sind lediglich erlaubte Kandidaten.

Context7 lieferte die offiziellen Alpaca-Dokumente unter `/websites/alpaca_markets_us`; die konkreten REST-Endpunkte wurden direkt in der Primärreferenz nachgeprüft. Es wird kein neues Alpaca-SDK installiert: bestehendes gepinntes HTTPX 0.28.1, Trading-API v2 und Data-API v2/v1beta3. Die Kalender-/Assetantworten bleiben Laufzeitevidenz, keine statische Berechtigungszusage.

## Sichere lokale Prüfung

```powershell
.venv/Scripts/python.exe -m pytest tests/contracts/test_alpaca_market.py tests/security/test_market_gateway.py -q
```

Tests verwenden ausschließlich HTTPX MockTransport mit synthetischen Werten. Codex liest keine Broker-Credentialdatei und sendet auch keine privaten read-only Requests. Ein späterer Anwendungslauf konstruiert den Provider ausschließlich in Execution und injiziert Credentials dort sicher. Default ist deaktiviert; URL, GET-Routen, TLS-Prüfung, ausgeschaltete Umgebungsproxies und Redirects sind festgelegt. Worker erhalten ausschließlich die eingefrorene normalisierte Momentaufnahme über WorkerMarketFeed, keinen Client und keine Header.

Nach Konto-/Asset-/Daten-/Sitzungsfehlern keine alte erfolgreiche Antwort weiterreichen. Abgelaufene oder inkonsistente Momentaufnahmen sind gesperrt. Nur zwei benachbarte, abgeschlossene, frische 15-Minuten-Kerzen aus der erlaubten Sitzung werden freigegeben. Änderungen erzeugen neue Inhaltsidentitäten. Verteilungen, Positionsgrößen, Risk-Policy und Brokerorders gehören nicht zu diesem Adapter.

Der Gateway widerruft ältere Workerfeeds bereits vor einem Refresh über einen kleinen geheimnisfreien In-Memory-Generationszähler; ein fehlgeschlagener Refresh kann sie nicht wieder aktivieren. Dieser lokale Zähler ist kein dauerhaftes Berechtigungszertifikat und wird nicht über DB/Jobs serialisiert. Serialisiert werden ausschließlich normalisierte Snapshotfelder. Spätere Execution-/DB-Verbraucher müssen ihre aktuellen Konto-, Capability-, Sitzungs- und Ablaufgates unmittelbar vor Submit selbst prüfen. Ein Worker-Snapshot autorisiert niemals eine Order; der Alpaca-Ausführungspfad bleibt bis PTV-17 gesperrt.

Rate-Limits oder Timeouts liefern stabile interne Gründe ohne Anbietertexte. Wiederholung erfolgt nur in einem späteren neuen read-only Erfassungslauf, niemals durch Umleitung oder Wiederverwendung alter Berechtigungen. Ordertypen und fehlende Mengen-/Preisinkremente bleiben UNKNOWN; explizite positive Assetinkremente werden als Decimal übernommen. Mindestordergröße und weitere Executionregeln gehören zu PTV-17. Datenzugang ist keine Ausführungsfreigabe. Reale Feedberechtigung, Kontorechte und Anbieterperformance sind durch lokale Mocks ausdrücklich nicht zertifiziert. Größere Antworten als der feste Puffer werden sicher abgewiesen; insbesondere der reale Umfang der Kryptoliste bleibt ungeprüft.
