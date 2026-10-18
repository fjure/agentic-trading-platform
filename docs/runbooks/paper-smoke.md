# Menschlicher Opt-in Paper-Smoke

Stand2026-10-18, PTV-22. Ausschließlich ein menschlicher lokaler Operator startet diesen Ablauf in privaten interaktiven Terminals. Entwicklungsagenten führen weder die folgenden Befehle mit Anbieterkonfiguration noch echte Providerrequests oder Orders aus. CI prüft nur Offlinefixtures. Ein tatsächlicher Paperlauf wurde nicht ausgeführt; die technische Vorbereitung dieses Tasks wird separat bewertet. LIVE bleibt gesperrt.

## Vorbedingungen

PTV-17/24/25 zertifizieren Adapter, dauerhafte Konto-/Capabilitybindung und vollständige Order-/Activities-/Gebühren-Reconciliation offline. PTV-21 stellt getrennte Rollen, Mounts, Kontrollsockets und feste Egress-Proxys bereit. Dieser Ablauf ersetzt diese Nachweise nicht; technische Evidenz: `docs/evidence/ptv-22-paper-smoke.md`.

- Dediziertes Paper-only-Konto, USD, Retail-Cashprofil, keine Positionen, unbekannten/offenen Orders, Jobs oder Reservierungen. Die eingefrorene finanzielle Ledgerstartbasis muss exakt zur Brokerbasis passen. Das Fake-Lab startet technisch mit10000USD, Alpaca standardmäßig mit100000USD: das ist kein austauschbarer Brokerbeleg. Bei Abweichung abbrechen; kein automatisches Rebase, keine Löschung bestehender Volumes/Orders/Ledger. Eine passende dedizierte saubere Labor-/Paperbasis richtet der Mensch vorher ein.
- Ausgangszustand `DISABLED`/`PAUSE_NEW`, positive aktuelle Policyversion, bestätigte SPY-/QQQ-Capabilities, frische geschlossene15-Minuten-Kerzen/Quotes, reguläre New-York-Sitzung. Außerhalb des Frischefensters abbrechen; keine Zeit-/Frischegates umgehen.
- Operator bestätigt das unveränderliche Profil `alpaca-paper-us-retail-equity-zero-v1`: selbstverwaltetes individuelles Retail-Cashkonto, US-Aktien/USD, keine Partner-/Elite-/Non-Retail-Provisionen, kein Krypto. Unbekannte oder widersprüchliche FEE/CFEE sperren den Abschluss. Der1%-Cashpuffer ist kein Gebührenbeleg.
- Private `.tooling/application/runtime/alpaca.json` ausschließlich durch den Operator einrichten: genau `api_key`, `api_secret`, `expected_account_id`, restriktive Ownerrechte/Linux0600; Mount nur Execution. Keine Werte in CLI, Chat, Browser oder Logs. Keine freie Base-URL. Öffentliche Kontoidentität im Ablauf ausschließlich als vom Menschen vorab lokal berechneter SHA256-Fingerprint.
- Alle Migrationen einschließlich0011 vorher anwenden. `scripts/start-local-lab.ps1 -ProvisionOnly` ist ein vorheriger Fake-/DISABLED-Schritt mit vorhandenen Mount-/ACL-Prüfungen, migriert vorwärts und überschreibt keine bestehende finanzielle Basis. Erst danach öffentliche Runtimekonfiguration einstellen. Der Launcher verweigert absichtlich einen Paperstart.

Öffentliche `.tooling/application/runtime/runtime.json` für diesen Ablauf:

```json
{
  "mode": "DISABLED",
  "broker_provider": "alpaca-paper",
  "decision_provider": "mock",
  "account_id": "paper",
  "instruments": ["SPY", "QQQ"],
  "origin": "http://127.0.0.1:8000",
  "paper_enabled": true,
  "reconciliation_fee_profile": "alpaca-paper-us-retail-equity-zero-v1"
}
```

Konfiguration allein aktiviert keinen Handel. Normale Execution und Worker vorher stoppen; Control API, Watchdog und Postgres bleiben unabhängig. Kein normaler Queueconsumer darf parallel laufen. Die folgenden PowerShellbefehle sind ausschließlich vom Menschen auszuführen; öffentliche Platzhalter vorher ersetzen.

```powershell
docker compose -f infra/compose/compose.yaml --profile lab stop execution worker
docker compose -f infra/compose/compose.yaml --profile lab build execution worker control-api watchdog
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.paper.yaml --profile paper up -d postgres control-api watchdog execution-egress
```

Der Execution-Proxy erreicht nur feste offizielle Data-/Paperhosts. Der Worker-Einmalpfad verwendet die Basiskonfiguration ohne Jev-Key oder Worker-Egress. Fehlende private Datei, falsche SQL-Rolle oder fehlende Migration beendet den Ablauf; keine Rechte erweitern.

## Einmaliger Ablauf in drei privaten Terminals

1. TerminalA: neue kanonische UUID für genau diesen Lauf wählen. Limitpreis ausdrücklich vom Menschen festlegen; keine aus Kerzen erfundene Quote und kein automatisch erhöhter Preis. Menge genau ein ganzer SPY- oder QQQ-Anteil, BUY/LIMIT. `max-notional` höchstens1000USD und mindestens `1 × limit-price ×1.01`; normale Portfolio-/Riskgrenzen gelten zusätzlich. Unterhalb eines zulässigen ganzen Anteils abbrechen, kein Fractional-/Market-Fallback. Technische Simulationsgrenzen, keine Handelsempfehlung.

```powershell
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.paper.yaml --profile paper run --rm --no-deps --entrypoint python execution -m services.execution.paper_smoke --paper-only --operator-confirmed --run-id RUN_UUID --account-fingerprint ACCOUNT_SHA256 --instrument SPY --limit-price LIMIT_DECIMAL --max-notional BUDGET_DECIMAL
```

TTY nicht mit `-T` abschalten oder Eingaben pipen. Flags, Zahlen, Fingerprint und Linux-Vordergrundterminal werden vor privaten Konfigurationszugriffen geprüft. Phrase `PAPER RUN_UUID` persönlich eintippen. Der dauerhafte Startbeleg sperrt dieselbe Run-ID auch nach Abbruch. Execution vergleicht den Fingerprint vor Providerkontakt mit der privat konfigurierten Konto-ID; der Adapter bestätigt zusätzlich die tatsächliche Kontoidentität am festen Paperhost. Verifizierte Rohbars dürfen bei geschlossener Latch gespeichert werden; ein freigegebener MarketState entsteht erst nach Recovery.

2. TerminalB: erst wenn TerminalA dies verlangt, frischen lokalen Aktivierungsrequest mit der aktuellen Policyversion auslösen:

```powershell
docker compose -f infra/compose/compose.yaml --profile lab exec control-api python -m services.execution.control_socket --socket /run/control/control.sock activate-paper --policy-version POLICY_VERSION
```

Die zurückgegebene `request_id` in TerminalA eingeben. Der Prüfrequest ist kein Erfolgsbeleg. Execution verarbeitet nur diese nach Laufbeginn entstandene, noch gültige ACTIVATE_PAPER-Anfrage mit normalen Policy-/Pausegenerations-, Owner-/Fencing-, Markt-/Sitzungs-/Konto-/Portfoliogates. HALTED wird nicht entsperrt. Beim Warten erneuert Execution den Ownerheartbeat; es fordert selbst keine Aktivierung an.

3. TerminalA zeigt öffentliche `market_state_id` und `request_sha256`. TerminalC startet den getrennten Worker für genau diesen State mit denselben Laufparametern:

```powershell
docker compose -f infra/compose/compose.yaml --profile lab run --rm --no-deps --entrypoint python worker -m services.worker.paper_smoke_plan --paper-only --operator-confirmed --run-id RUN_UUID --account-fingerprint ACCOUNT_SHA256 --instrument SPY --limit-price LIMIT_DECIMAL --max-notional BUDGET_DECIMAL --market-state-id MARKET_STATE_ID
```

`PLAN RUN_UUID` persönlich bestätigen. Worker bekommt nur seinen DB-Mount. Ein auf DB-Ebene Execution vorbehaltener Auditbeleg bindet State, Run, Instrument, Limit und Budget für120Sekunden; Worker sieht nur dessen begrenzte öffentliche Projektion. Der vorhandene Mock-/Baselinepfad beurteilt echte normalisierte Beobachtungen. FLAT/ABSTAIN erzeugt keinen LONG-Vorschlag: dann abbrechen. Ein LIMIT-Vorschlag wird vor Persistenz endgültig gebildet, niemals später verändert. Ausschließlich die ausgegebene deterministische Proposal-ID in TerminalA eingeben. Abgelaufene Autorität oder Markt-/Decision-/Proposal-/Risk-TTLs werden nicht verlängert.

Execution reserviert normales Riskbudget und übermittelt genau diesen Intent einmal; weder `tick()` noch normale Proposal-/Intentqueues werden ausgeführt. Anschließend PAUSE_NEW, auch bei Ctrl-C, EOF oder Fehler. Kein automatisches Wiederholen, Repricing, Resume, Marketorder oder Flatten.

## Cancel, Abschluss und Incident

Begrenzte Status-/Activities-/Positions-/Kontoprüfungen folgen. Eine bekannte offene Order erhält höchstens einen vor DELETE dauerhaft auditierten Cancelversuch. `204` bestätigt nur die angenommene Anfrage; CANCEL_PENDING ist kein Abschluss. Kumulative Fills und vollständige Reconciliation lösen Fill-/Cancelrennen. Unbekannte Gebühren, Teilbelege, Widerspruch, Timeout oder fehlende Autorität führen zu `UNKNOWN` und gehaltener Reservierung, niemals erneutem POST. Eine bestätigte Position wird nicht automatisch glattgestellt.

Nur belegtes FILLED/CANCELED/REJECTED mit vollständiger Reconciliation ist terminal; der Lauf bleibt pausiert. Ein begrenzter Outboxpublisher stellt Ereignisse dem vorhandenen Snapshot-/SSE-Pfad bereit. Für den Browservergleich die lokale UI separat nach Betriebsrunbook zugänglich machen; fehlender UI-Transport ist kein bestandener Sichtvergleich.

Menschlicher Report: UTC-Zeiten, Run-/Trace-ID, redigierte Konto-/Orderkennung, Paperhostklassifikation, Policy-/Capability-/Contractversion, Limit/Budget, Proposal→Risk→Reservation→Intent→Order→Report, Cancelbeleg, gebuchte Mengen/Cash/Gebühren, Reservierungsrest, Reconciliation, Pausezustand und Outbox-/SSE-/Dashboardvergleich. Keine privaten Payloads/Schlüssel. Fehlende Evidenz markieren. Ungeklärte Orders bleiben Incident nach `recovery.md`; nicht einfach mit neuer Run-ID fortfahren.

## Technische Prüfung und Quellen

```powershell
.venv/Scripts/python.exe -m pytest tests/paper/test_smoke.py tests/unit/test_paper_smoke_plan.py tests/integration/test_paper_smoke.py -q
```

Nur synthetische MockTransports und UUID-isolierte PostgreSQL-Datenbanken, kein echter Paperlauf. Historische Integrationsblocker in PTV-17/24/25 gelten nach deren späterem Abschluss nicht mehr; deren Evidenz bleibt getrennt von realer Providerverifikation.

Offizielle Primärquellen am2026-10-18 erneut geprüft, Context7 `/websites/alpaca_markets_us` und direkte Dokumentationsseiten: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Cancel204/422](https://docs.alpaca.markets/us/reference/deleteorderbyorderid-1), [Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [Activities](https://docs.alpaca.markets/us/docs/account-activities), [Retailkommissionen](https://alpaca.markets/support/commission-clearing-fees). Paper nutzt eigene Schlüssel und simulierte Ausführungen; Markteinfluss, Queueposition, Latenzslippage, regulatorische Gebühren und Dividenden sind kein nachgewiesenes reales Verhalten. APIv2, vorhandenes HTTPX0.28.1, keine neue SDK-Abhängigkeit. Fehlende/widersprüchliche Providerautorität bleibt geschlossen.
