# Opt-in Paper-Smoke (später)

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
