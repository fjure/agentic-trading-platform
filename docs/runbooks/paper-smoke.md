# Opt-in Paper-Smoke (später)

## PTV-24: SQL-Substrat, noch keine Betriebsfreigabe

Stand2026-10-17: Die dauerhafte Alpaca-Executionbindung besitzt private Konto-/Asset-/Sessionbelege und eine kanonische Trading-Capabilityversion. Vor I/O wird SUBMITTING dauerhaft geschrieben; Restore behandelt vorhandene Versuche auch bei 404 als möglicherweise gesendet. Erste Bindung oder geänderte Provider-/Capabilitybelege setzen die bisherige Recoveryautorität atomar außer Kraft. Selbst ein ACK bleibt mit PENDING und unbekannten Gebühren sichtbar. Der normale Alpaca-Prozess bleibt pausiert und verweigert Recovery mit ALPACA_RECOVERY_PENDING, bis PTV-25 vollständige Inventar-/Activities-/Gebührenautorität belegt. PTV-21 liefert danach die echte Prozessverdrahtung. Keine Fake-Testfreigabe und kein temporäres Journal ersetzt diese Voraussetzungen. Alle bisherigen PTV-24-Requests erreichten ausschließlich synthetischen MockTransport.

## PTV-17: lokaler Adaptervertrag

Stand 2026-10-15; offizielle Quellenprüfung 2026-10-14. Der Adapter unter `packages/brokers/alpaca_paper.py` wird ausschließlich mit synthetischen Werten und HTTPX MockTransport geprüft. POST-/DELETE-Operationen dieser Tests erreichen nur den lokalen Handler. Dies ist keine Erlaubnis für Codex, echte Paper-Requests oder Orders auszuführen.

Primärquellen: [Orderanfrage](https://docs.alpaca.markets/us/reference/postorder), [Client-ID-Abfrage](https://docs.alpaca.markets/us/reference/getorderbyclientorderid), [Storno](https://docs.alpaca.markets/us/reference/deleteorderbyorderid-1), [Orderregeln](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [Fractional](https://docs.alpaca.markets/us/docs/fractional-trading), [Crypto](https://docs.alpaca.markets/us/docs/crypto-orders) und [Activities](https://docs.alpaca.markets/us/docs/account-activities). Context7 lieferte `/websites/alpaca_markets_us`; die konkreten Endpunkte wurden zusätzlich direkt geprüft. Bestehendes HTTPX 0.28.1 und Trading-API v2; keine neue SDK-Abhängigkeit.

Der lokale Requestvertrag verwendet ausschließlich den festen Paperhost, Mengen-/Preisstrings, Client-ID, einfache Market-/Limitorders, DAY für Aktien und GTC für BTC/USD. Extended Hours, Brackets, Shorts, Notionalorders und fremde Instrumente sind gesperrt. DELETE 204 bedeutet nur angenommene Stornoanfrage; erst ein späterer Statusabruf kann CANCELED/FILLED belegen. Timeout,429 und andere unklare Antworten halten die Client-ID für Recovery zurück; es erfolgt kein automatischer zweiter POST. Interne Orderidentität und Broker-UUID bleiben getrennt.

Das konservative Profil erlaubt BUY nur als begrenzte Limitorder, SELL Market/Limit nur gegen bestätigte verfügbare Long-Menge. BUY Market wird wegen fehlender Preisobergrenze im OrderIntent abgewiesen. Die Quellen widersprechen sich bei fractional Limit teilweise; dieses Profil nimmt die engere Kombination fractional MARKET/DAY und erlaubt damit ausschließlich fractional SELL. Crypto benötigt bestätigte Mindestmenge und Inkremente, zusätzliche Aktivierung sowie ein aktives Crypto-Konto. Diese Einschränkungen sind Teil des Adapterprofils; generische Capabilityflags garantieren keine beliebige Kombination aus Seite, Ordertyp und Bruchstückmenge.

Die zusätzliche Cashreserve ist ein expliziter Decimal-Konfigurationswert `cash_cushion_fraction` (Default 0.01, gültig größer 0 bis einschließlich 1) und fließt in die Capabilityversion ein. Sie ist ein konservativer Puffer, kein bestätigter Gebührenbetrag. Aktien benötigen Kalender-/Clockbelege und eine letzte Prüfung der Frist und regulären Sitzung unmittelbar vor POST. Auch Status-/Stornoaufrufe prüfen die Kontoidentität frisch. Eine reine Einstiegssperre des richtigen Kontos soll dessen bestehende Orders weiterhin lesbar und stornierbar lassen.

Vor jedem Submit muss Execution den unveränderlichen Intent und den möglichen Sendeversuch dauerhaft speichern. Nach Neustart werden beide mit `restored_intents` und `attempted_client_ids` wiederhergestellt; `bind_intent(..., attempted=True)` ist ebenfalls ein reiner Leserecoverypfad. Der Adapter ist kein dauerhaftes Ledger. Selbst eine 404 erlaubt für eine bereits versuchte Client-ID keinen neuen POST. Ein lokaler Dateijournaltest belegt diesen Wiederanlaufvertrag mit synthetischem Zustand, keine produktive DB-Integration.

Statusabfragen liefern kumulative Ausführungsdaten. Ein Order-GET besitzt keinen autoritativen Gesamtgebührenbeleg: `fee=None` und `Reconciliation.PENDING` bleiben erhalten. Solche Reports dürfen weder Reservierungsfreigabe noch einen abgeglichenen Cashbestand behaupten. Activities-/Gebührenzuordnung und sämtliche dauerhaften Execution-/Account-/Pausegates sind vor einer realen Paperfreigabe zusätzlich zu integrieren und zu prüfen. Die vorhandenen SQL-Submit-/Reconciliationpfade sind weiterhin Fake-only. Dieser offene Integrationspunkt blockiert den späteren Operator-Smoke und die abschließende Gesamtverifikation; ein bestandener lokaler Adaptertest ersetzt ihn nicht.

PTV-16 und PTV-17 besitzen unterschiedliche Capabilityprofile und Versionsdigests. Beim späteren Anschließen an Risk/Execution dürfen diese Versionen nicht als austauschbar behandelt werden; Konto-, Asset-, Daten- und Sitzungsbelege müssen ausdrücklich gemeinsam gebunden werden. Lokale Brokerporttests belegen diese paketübergreifende Integration noch nicht.

```powershell
.venv/Scripts/python.exe -m pytest tests/contracts/test_alpaca_paper.py tests/contracts/test_fake_broker.py tests/recovery/test_alpaca_fixtures.py -q
```

Der Paper-Smoke ist eine **spätere menschlich freizugebende Prüfung**, kein Teil normaler CI und keine Aufforderung, jetzt eine Order zu senden. Er darf erst nach lokalem Fake-Durchstich, Brokeradapter-Zertifizierung, vollständiger Runtime-Sicherheitsprüfung und ausdrücklich bereitgestelltem Paper-Konto stattfinden. Nur ein verifizierter Paper-Endpunkt und ein Paper-only-Konto sind zulässig; LIVE bleibt technisch blockiert.

## Vorbedingungen und harte Abbruchregeln

1. Menschlicher Operator bestätigt Testfenster und minimales Simulationsbudget. Explizites Paper-Flag, Konto-ID-Fingerprint und fest eingebaute Paper-Host-Allowlist werden vor dem Start geprüft; fehlen sie, erfolgt **null Netzwerk-Submit**. Eine übergebene freie Base-URL ist unzulässig.
2. Execution besitzt allein die restriktiv gemounteten Paper-Credentials. Kein Key in CLI-Argument, Terminalhistorie, Browser, Prompt, Testreport oder Log. Agent, Control API und Dashboard besitzen weder Broker-Submit-Port noch Credentials.
3. System ist vor Aktivierung `DISABLED`/`PAUSE_NEW`. Health, Datenfrische, Session, Capabilities, Policy, verfügbare Equity und vollständige Reconciliation müssen grün sein. Unbekannte Brokerorder, Ledgerdrift oder unbestätigte Asset-/Orderfähigkeit bricht den Smoke ab.
4. Der spätere Test wird nur durch eine eigens implementierte, überprüfte Opt-in-Schnittstelle gestartet. **Geplanter Befehlsplatzhalter, derzeit nicht ausführbar:** `tradingctl paper-smoke --paper-only --operator-confirmed`. Die tatsächliche CLI muss zusätzlich eine interaktive oder gleichwertig nachweisbare Bestätigung und feste Minimalgrenzen haben. Niemals Codex, MCP oder Agenten für Brokerorders einsetzen.

## Geplanter Ablauf

1. Einen kleinen, innerhalb der bestätigten Fähigkeit zulässigen LONG/FLAT-Vorschlag aus frischer abgeschlossener Kerze erzeugen. Die deterministische RiskDecision samt Reservation, Intent, `clientOrderId`, Trace-ID und Audit vor Submit prüfen.
2. Execution übermittelt höchstens eine minimale Paper-Order. Den Brokerstatus und Teilfüllungen beobachten; bei Timeout sofort `UNKNOWN`, Reservierung halten und `recovery.md` anwenden. Kein blinder Retry.
3. Falls offen und zulässig, Storno anfordern; andernfalls definitiven Fill festhalten. Cancel/Fill-Rennen werden über kumulative Fills und Broker-Reconciliation aufgelöst. Keine automatische Flatten-Marketorder.
4. `PAUSE_NEW` setzen, vollständige Reconciliation durchführen und Ledger, Position, Reservierung, Outbox, SSE, Dashboard und Trace-Audit vergleichen. Test endet nur mit eindeutigem Status oder ausdrücklich offenem UNKNOWN-Incident.

## Abnahmekriterien und Evidenz

Höchstens eine brokerseitige Order je Intent; keine reale Orderadresse; keine Broker-Credentials außerhalb Execution; alle Werte als Decimal/UTC; eine lückenlose Trace-Kette von MarketState bis Report; keine doppelte Fillbuchung; kein Secret-Canary in Log/API/UI; nach Test keine ungeklärte neue Freigabe. Report enthält nur redigierte Konto- und Orderkennungen, Hostklassifikation `paper`, Policy-/Contractversion, UTC-Zeiten, Status und Reconciliationergebnis. Jeder fehlgeschlagene Guard oder unbekannte Brokerstatus beendet weitere Submits und hält PAPER pausiert.
