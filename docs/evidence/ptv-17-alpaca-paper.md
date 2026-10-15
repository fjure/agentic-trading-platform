# PTV-17: lokaler Alpaca-Paper-Brokerport

Prüfstand 2026-10-15; offizielle Quellen vom 2026-10-14 im [Runbook](../runbooks/paper-smoke.md). Requirements REQ-RSK-003/008/009/010 und REQ-RUN-002/006. Dieser Nachweis betrifft den offline geprüften Adaptervertrag; vollständige Paper-Laufzeitintegration und ein menschlicher Operator-Smoke sind nicht dadurch zertifiziert.

## Verhalten und Prüfungen

`AlpacaPaperBrokerAdapter` verwendet den festen Paperhost mit expliziter Aktivierung und gepinnter kanonischer Konto-UUID. Ohne Flag, Identität oder Credentials erfolgt kein Kontakt. Tests verwenden ausschließlich synthetische Werte und exakten HTTPX MockTransport. POST/DELETE erreichen nur dessen lokalen Handler. LIVE, freie URLs, fremde Transportinjektion, Redirects und Umgebungsproxies sind ausgeschlossen. HTTPX 0.28.1 und Trading-API v2; keine neue SDK-Abhängigkeit.

Vor jedem Orderpfad wird die Kontoidentität erneut bestätigt. Orderlesung und Storno bleiben beim richtigen, nur für neue Trades gesperrten Konto möglich. Vor POST werden Cash, verfügbare Long-Menge, Asset/Capabilityversion, Brokerzeit und reguläre Sitzung einschließlich Kalender/DST/verkürztem Schluss geprüft; abgelaufene Belege werden auch nach den letzten I/O-Aufrufen verworfen. Crypto benötigt zusätzlich Opt-in, Kontoaktivierung und bestätigte Mindestmenge/Inkremente. Der Cashpuffer ist konfigurierbar, positiv und Bestandteil des Versionsdigests.

| Prüfung | Ergebnis |
|---|---|
| Kombinierte lokale Suite | 298 passed in 5.74s: Contracts, Domain, Risk, Gateway, Replay, Shadow und Paper-Recovery |
| Paper-Recoveryvertrag | sechs Fälle: Neustart nach simuliertem Prozessabbruch vor/nach Annahme, Read-only-Reconnect, Teilfill/Cancel/Latefill/Dubletten und gemeinsamer Fake-/Alpaca-Brokerport |
| Präzision und Identität | Decimal/UTC, frische Konto-/Assetbindung, rohe JSON-Validierung, Client-/Broker-ID-Abgleich, immutable Intentkonflikte und inhaltsgebundene Report-IDs über Neustarts |
| Lint | Ruff für Adapter, Contracttests und Recoverytests bestanden |
| Zusätzliche PG-Regression | vollständiger Wiederholungslauf nach Docker-Reparatur: 8 passed in 139.77s; tatsächliche Rollen-/Startupgates sowie Crash-before/Crash-after/Timeout bestanden. Erster Versuch: 2 passed, 6 Infrastrukturfehler vor Prüfung wegen fehlender Engine |
| Source/Plan/OpenSpec | 294 Quelltextdateien, 0 Credentialmuster; 40REQ/23-Task-DAG und strikte Validierung ohne Befund |

Der Wiederanlauftest schreibt ausschließlich einen synthetischen Intent und die Versuch-ID vor I/O in ein temporäres lokales JSON-Journal, verwirft den Adapter und konstruiert ihn aus wiederhergestelltem Zustand neu. Sowohl nach serverseitiger Annahme als auch bei 404 bleibt die POST-Zahl eins. Dies belegt die Restore-Schnittstelle, keine produktive dauerhafte Ledgeranbindung. RLock verhindert konkurrierte Doppelübermittlung derselben Client-ID innerhalb einer Instanz; kontoübergreifende Transaktionssperren bleiben Aufgabe von Execution.

## Safetyreview

| Invariante | Befund und Grenze |
|---|---|
| Unklarer Submit wird nicht wiederholt | Timeout/429/422/500 führen zu UNKNOWN; wiederholter Aufruf und wiederhergestellter Versuch verwenden GET statt POST |
| Broker-/Client-ID und Zustände | Beide Lookupwege werden abgeglichen; fremde IDs, sinkende kumulierte Menge und Terminalregression werden abgewiesen; DELETE204 ist nur CANCEL_PENDING |
| Keine Short-/Marginfreigabe | SELL nur gegen frische verfügbare Long-Menge; BUY als Limit mit Cashpuffer; BUY Market mangels Preisobergrenze gesperrt |
| Keine erfundenen Kosten | Order-GET liefert keinen Gesamtgebührenbeleg; Reports bleiben fee=None und Reconciliation.PENDING; keine Reservierungs-/Cashfreigabe daraus ableiten |
| Risk-/Executiongrenze | Vorhandene SQL-Ausführung bleibt Fake-only; kein neuer Agenten-, Control- oder Worker-Submitpfad |
| Nachvollziehbare Evidenz | Normalisierte Reports binden Broker-ID, Status, Mengen, Durchschnittspreis und Quellzeit; identische Beobachtungen bleiben über Neustarts idempotent |

Unabhängiger Quellreview hat frühere Accountwechsel-, Frische-, Storno-, Terminal-, Reportidentitäts- und Konfigurationsbefunde mit gezielten Regressionen nachgeprüft. Der letzte Metadatentest bindet Crypto-Mindestmengenänderungen in die Version und verhindert irrelevante Versionswechsel beim Öffnen/Schließen der Aktienbörse.

## Offene Gesamtintegration

Der Adapter ist noch nicht an die dauerhaften SQL-Submit-/Reconciliation-/Recoverygates angeschlossen. Activities-/Gebührenautorität, persistente Versuchsanbindung und gemeinsame Konto-/Asset-/Sitzungs-/Capabilityversionen von PTV-16/17 sind vor realem Paperbetrieb zu integrieren. Diese Arbeit bleibt eine harte Voraussetzung für PTV-21/22 und die abschließende Verifikation. Frühere Fake-Recoverybelege oder das hier synthetische Dateijournal erfüllen diese Voraussetzung nicht stellvertretend.

Das Profil unterstützt Market/Limit nicht beliebig für jede Seite/Menge: BUY ist ausschließlich Limit, fractional Equity ausschließlich MARKET/DAY und somit SELL; Crypto verwendet GTC. Unsupported-Kombinationen werden vor Übermittlung abgewiesen. Keine reale Berechtigung, Paper-Ausführung, Kostenfreiheit oder Ausführungsqualität wurde getestet oder behauptet. Agenten führen auch später keine Brokerorders aus.
