# PTV-16: Alpaca-Marktdaten und Fähigkeiten

Prüfstand 2026-10-14. REQ-MKT-001/002/003/005/006/008, REQ-RSK-003, REQ-RUN-006; zugehörige T-MKT-/T-RSK-/T-RUN-Nachweise aus tasks.md. [Runbook und offizielle Primärquellen](../runbooks/alpaca-data.md). Trading-API v2, Data-API v2 und v1beta3, bestehendes HTTPX 0.28.1. Keine neuen Abhängigkeiten, Credentials, Broker-/Order-MCPs oder externen Requests.

## Implementierung

`AlpacaMarketDataProvider` startet deaktiviert, erlaubt SPY/QQQ und BTC/USD nur nach zusätzlichem Opt-in und aktuellen read-only Nachweisen. Fester Paperhost für Konto/Asset/Uhr/Kalender, fester Datenhost und expliziter Aktienfeed. Konto-/Assetidentitäten müssen kanonische UUIDs sein; sie werden intern in die Capabilitykennung gebunden, nicht als Kontopayload veröffentlicht. Fehlende oder gesperrte Rechte, unbekannte Symbole und inkonsistente Sitzung führen zu sicherer Ablehnung. Ein Aktienlookup liest nur SPY beziehungsweise QQQ; Krypto nutzt die gefilterte Assetliste.

Worker erhalten Instrument, Daten-/Brokerfähigkeiten, zwei abgeschlossene Kerzen, UTC-Bezugszeit, Ablaufzeit und Sitzungsgrenzen. Kein HTTP-Client, Authheader oder Provider-Rohpayload befindet sich in der Momentaufnahme. Gatewayfeeds prüfen Ablauf und aktuelle lokale Generation bei jeder Benutzung. Vor Refresh wird der alte Feed gesperrt; bei Fehlern werden alle Instrumentenfeeds desselben Kontos widerrufen. Erfolgreiche getrennte SPY-/QQQ-Feeds können gleichzeitig gültig bleiben. Diese lokale Lease wird nicht in DB/Jobs serialisiert und ist kein Orderberechtigungsnachweis.

Baridentitäten binden Feed, Instrument, Zeit und exakte Werte; Korrekturen ändern die Identität. Empfangs-/Verfügbarkeitszeit wird erst nach Daten-I/O gesetzt. Finaler Zeitabgleich begrenzt Rechte, Brokeruhr, Datenalter und Sitzung auch bei langsamen Antworten. Die beiden abgeschlossenen Kerzen sind benachbart, vollständig, plausibel und innerhalb der regulären Sitzung. Kryptofenster dürfen ohne künstliche Pause über UTC-Mitternacht reichen. Positive explizite Assetinkremente werden exakt als Decimal übernommen; fehlende bleiben unbekannt. Market-/Limitordertypen bleiben UNKNOWN und `permits()` kann keine Order freigeben.

## Ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| Kombinierte Contract-/Domain-/Risk-/Replay-/Shadow-/Gatewayregression | 268 passed in 4.74s; enthält 57 Alpaca- und 15 Gatewayprüfungen |
| Executionisolation | 5 passed in 73.26s; LIVE ausgeschlossen, Worker-/Controlimporte getrennt, drei tatsächliche PostgreSQL-Rollen-/Guard-/Startupfälle ohne Brokerzugriff |
| Lint | Ruff für alle vier neuen Pythondateien bestanden |
| Source/Plan/OpenSpec | 290 Quelltextdateien, 0 Credentialmuster; 40REQ/23-Task-DAG und strikte Validierung ohne Befund |

Die Adapterfälle prüfen Konto-/Crypto-/Assetrechte, unbekannte Instrumente, kanonische IDs, Feed403 ohne Fallback,429,Timeout, verspätete/future/stale Uhr, DST und verkürzten Börsentag, doppelte/lückenhafte/vertauschte/offene Kerzen, vollständige Pagination und wiederholte Cursor, Roh-JSON-Dezimalzahlen mit 18 versus 19 Nachkommastellen, nicht endliche Werte, doppelte Schlüssel und tief verschachteltes JSON. Gatewayprüfungen decken Credentialfreiheit, immutable DTOs, Manipulation, TTL, unbekannte Session/Cursor, Widerruf und tatsächliche normalisierte Mock-Adapterübergabe ab.

## Safety und Grenzen

| Invariante | Befund/Evidenz |
|---|---|
| Kein Agenten-Brokerzugriff | ausschließlich GET-Allowlist ohne Orders/Submit; Workerport hält kein Providerobjekt; AST-/Rollenprüfungen bestanden |
| Deterministische Risk-/Executiongates | keine Änderungen an Risk, Reservations oder Submit; unbestätigte Orderfähigkeiten verhindern `permits()` |
| LIVE/default fail-closed | fester Paperhost, disabled Default, BTC zusätzlich Opt-in, LIVE-Negativtest bestanden |
| Decimal/UTC/Verfügbarkeit | exakte NUMERIC(38,18)-Werte, UTC/IANA-DST, nach I/O gestempelte Evidenz, finale Frische-/Sessionprüfung |
| Secrets/Transport | nur Execution-injizierte Credentials; Default TLS, keine Env-Proxies/Redirects, nur exakter MockTransport injizierbar; stable redacted Fehler |
| Pause/Reconciliation | bleiben von diesem Datenadapter unabhängig; Brokerisolation-/Startupregression grün |

Unabhängiger Abschlussreview ohne verbleibenden Befund; frühere Clock-/Verfügbarkeits-, Transport-, Identitäts- und Widerrufsprobleme durch gezielte Regressionen geschlossen. Dieser Nachweis zertifiziert das lokale read-only Mapping mit synthetischen Antworten. Keine reale Kontoberechtigung, Echtzeitfeedfreigabe, Anbieterperformance oder Paper-Ausführung wurde behauptet. Große Antworten werden sicher abgewiesen; der reale Umfang der Kryptoliste ist ungeprüft. Capabilitykennung ist eine begrenzte Änderungskennung, kein kryptografisches Berechtigungszertifikat. PTV-17 muss aktuelle Execution-/DB-Gates, Mindestordergröße, Ordertypen und Recovery separat zertifizieren; frühere Worker-Daten autorisieren keinen Submit.
