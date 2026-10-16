# PTV-18: lokaler HTTP-Kontrolladapter

Prüfstand 2026-10-16. Requirements REQ-CTL-001/002/003/005/007, Design D-08 und kanonischer Vertrag `contracts/http/control-api.openapi.yaml`. Implementiert sind acht Session-/Mode-/Safety-/Policy-Operationen. Historienlisten, Snapshot und SSE folgen PTV-19; Browserformular und Dashboard PTV-20; Prozessverdrahtung PTV-21. Keine Broker-/Jev-Requests oder Orders wurden ausgeführt.

## Verhalten und Grenzen

`create_app` erhält ausdrücklich `LocalAuth` und den PostgreSQL-Controlport. Der Modulstandard konfiguriert keine Datenbank und verweigert Kontrollwirkungen. Host, Origin, tatsächliche lokale Server-/Peeradresse und JSON-Contenttype werden geprüft; Proxyheader, doppelte Sicherheitsheader und mehrdeutige Cookies werden verworfen. Keine CORS-Freigabe, öffentlichen Swaggerseiten oder Order-Submitroute. Responses sind interne, streng validierte DTOs; Fehler sind redigierte RFC9457-Probleme einschließlich unterscheidbarem UNKNOWN und Retry-After bei 503.

Der einmalige Launcher-Code verfällt nach fünf Minuten, Aktivierungsversuche sind begrenzt. HttpOnly/SameSite=Strict, bei HTTPS zusätzlich Secure; Sitzungen und Aktivierungs-Replayfenster gelten 24 Stunden. Ein Retry stellt dieselbe Sitzung wieder bereit und verlängert die serverseitige Frist nicht. Neustart verwirft die lokale Sessionauthority. Keine Tokenwerte wurden aufgezeichnet.

Kontrollwirkungen und unveränderliche Replaybelege werden unter dem bestehenden Accountlock atomar gespeichert, zusammen mit Audit/Outbox. Actor/Operation/Key und Payloadhash verhindern Zweitwirkung und erkennen abweichende Nutzlast. Belege werden zunächst nicht gelöscht und erfüllen damit die Mindestaufbewahrung von 24 Stunden. Der Replaycheck erfolgt vor der aktuellen If-Match-Prüfung. Policyupdates bleiben append-only, simulationsgebunden, exakt dezimal und mit starken ETags; fehlendes If-Match ergibt 428, ein veraltetes 412.

Die API kann weder RUNNING noch PAPER direkt setzen. PAPER/Resume legt nur einen befristeten Prüfauftrag an; bis zur bestätigten Execution-Recovery wird kein Erfolg behauptet. HALTED bleibt über die API unauflösbar. Ohne erste Policy wird ein Moduswechsel abgelehnt; Pause/Kill bleiben wirksam und kennzeichnen die fehlende Policy ausdrücklich als null. Ausschließlich diese beiden Notfallereignisse erlauben null; Recovery/Aktivierung verlangen weiterhin eine positive Version.

## Verifikation

| Prüfung | Evidenz |
|---|---|
| Finale lokale Suite | 365 passed in 5.41s: Contracts, Sessions, Gateway, Domain, Risk, Replay, Shadow und Paper-Recovery |
| HTTP-Transport | 25 ASGI-Vertragstests: DTOs/UTC/Dezimalstrings, Header/Trace, Problemcodes, 412/428, Fuzz-/Schema-/JSON-Negative, kein LIVE/Submit |
| Sessiongrenze | 29 Tests einschließlich Ablauf, einmaligem Verbrauch, parallelen Versuchen, Rategrenze, Replays, Host/Origin/Socket-/Cookie-Ambiguität |
| PostgreSQL-Controlport | Finaler Lauf beider Fälle: 2 passed in 46.56s; tatsächliche Rollen, Replay/ETags/Parallelität/Rollback, HTTP, Event-Decode, Decimal-Präzisionsabwehr und fehlende Policy |
| Ledger/Outbox/Safety | 24 unterschiedliche Fälle bestanden: 23 im Gesamtlauf (558.54s), session-closed-repair nach korrigierter Testabfrage separat 1 passed in 24.39s |
| HTTP-Vertrag | OAS3.1.2/18 Operationen, Redocly2.62.1 valide; oasdiff1.33.0 gegen main814ef736 ohne Breaking Changes |
| Interne Verträge | Zehn valide Schemas; Control-Event-Actions gebunden; Policyaggregat getrennt vom Kontoversionszähler |
| Client/Build | Generator7.26.0 zweimal65 bytegleiche Dateien; vollständiger Anwendungsbaum driftfrei, TypeScript und Angular-Produktionsbuild bestanden |
| Plan/Quelltext | 40 Requirements/23 geordnete Tasks; 307 Quelltextdateien ohne Credentialmuster; Ruff und OpenSpec strict bestanden |

Die gepinnte Starlette-/HTTPX-Kombination meldet eine DeprecationWarning für TestClient; Tests bestehen. Es wurde kein ungeplanter Dependencywechsel vorgenommen. Ein früher Fuzzgenerator erzeugte Infinity vor HTTP-Kodierung und wurde auf gültig kodierbare Zahlen begrenzt; nicht endliche rohe JSON-Werte werden separat abgewiesen. Erste neue PG-Fixtures scheiterten an den bestehenden Audittriggern; sie wurden durch passende synthetische Auditereignisse korrigiert, ohne Produktprüfungen zu lockern. Eine ältere Recovery-Testabfrage zählte nach dem seit PTV-13 vorhandenen SUBMITTING-Bericht zwei Reports; sie selektiert nun ausdrücklich den abgewiesenen SESSION_CLOSED-Bericht. Zero-submit- und PRE_IO_REJECTED-Auditprüfungen bleiben unverändert und bestanden. Windows-Sandboxläufe konnten vor ASGI beim asyncio-Socketpaar hängen; freigegebene lokale Läufe bestanden.

## Safetyreview

| Invariante | Nachweis |
|---|---|
| Isolation/kein Submit | Controlport importiert weder Execution noch Brokeradapter; SQL-Rolle besitzt keine uneingeschränkte Context-UPDATE-Freigabe |
| Deterministische Gates/LIVE | Zulässige Modi als Enum; SQL-Funktion erlaubt nur sichere Modi, echte Aktivierung ausschließlich über bestehende Execution-Prüfaufträge |
| Decimal/UTC | Strenge Transportmodelle, keine Floatlimits; SQL verwirft verlustbehaftete Nachkommastellen, UTC-Antworten |
| Unabhängige Pause/Kill | Persistenter Latch ohne Jev/UI/Brokerport; fehlende Policy darf Notfallaktion nicht blockieren |
| Wiederholung/Audit | Dauerhafter unveränderlicher Replaybeleg, Accountserialisierung, Audit-/Outboxtransaktion und Rollbacktest |
| Secrets | Keine automatischen Credential-/Umgebungslesevorgänge; redigierte Problemantworten und synthetische Canarytests |

Begrenzte Implementierungspakete verwendeten frischen Subagentenkontext; der Hauptagent integrierte. Ein unabhängiger Quellreview fand Retry-After-/Problemsemantik, kollidierende Eventversionen, widersprüchliche Sessionfristen und fehlende Policyguards. Nach gezielten Korrekturen wurden alle Befunde im Source-Review geschlossen. Zwischenzeitlich erreichte Subagenten-Nutzungslimits ersetzten keine Prüfung; PostgreSQL- und Gesamtevidenz stammen aus den tatsächlichen Hauptagentenläufen.

PTV-17 bleibt ein offline zertifizierter Brokerport. Dauerhafte Alpaca-Attemptanbindung, Activities-/Gebührenautorität und gemeinsame PTV-16/17-Capabilitybindung bleiben Voraussetzungen vor realem Paperbetrieb. Dieser HTTP-Task zertifiziert keine vollständige Paper-Runtime.
