# PTV-19: Snapshot, Historiencursor und SSE

Prüfstand 2026-10-16; REQ-CTL-004 und REQ-RUN-004/005, D-08. Alle 18 kanonischen HTTP-Operationen besitzen nun Transportadapter. Die Anwendung benötigt weiterhin die explizite Laufzeitverdrahtung aus PTV-21; die Benutzeroberfläche folgt PTV-20. Keine echten Broker-/Jev-Requests oder Orders wurden ausgeführt.

## Verhalten

Snapshot, Mode, Portfolio, Worker und jüngste eigene Orders sowie Publish-Watermark werden unter derselben PostgreSQL-REPEATABLE-READ-Sicht gelesen. Der opake Cursor wird in dieser Transaktion gespeichert. Eine bereits sichtbare, erst später publizierte Zustandsänderung darf nochmals als Event erscheinen; Event-ID und Aggregatversion ermöglichen Deduplizierung. Cursor beschreiben den Publikationsstand, nicht eine behauptete Exactly-once-Handelszustellung.

Die acht Historienlisten verwenden begrenzte Seek-Pagination. Die erste Sichtbarkeitsschranke und unveränderliche Erfassungsmetadaten halten die Mitgliedschaft fest: neue Commits und geänderte Heartbeats lassen keine Originalzeile verschwinden. Mutable DTOs zeigen den jeweils aktuell bestätigten Zustand. Cursor sind dauerhaft gespeichert, auf Konto/Ressource/Auditfilter begrenzt und sieben Tage gültig. Kein Netzwerkawait hält eine Datenbanktransaktion offen; Abfragen sind zeitlich begrenzt.

SSE prüft Cursor und erste Daten vor HTTP200, berücksichtigt Last-Event-ID vor Querycursor und sendet JSON-Daten mit id/event/data. Bei Leerlauf folgen Kommentarheartbeats im 15-Sekunden-Takt. Unbekannte/abgelaufene Cursor oder Lücken ergeben vor Beginn 410; spätere Lücken erzeugen reset-required und Streamschluss. Sessionwiderruf oder Datenbankausfall beendet den Stream, ohne Fehlertexte oder geratenen Zustand zu senden. Der Client muss neu verbinden beziehungsweise einen neuen Snapshot laden.

Migration0007 speichert neue historische Kontozuordnungen als unveränderliche Supervisor-Metadaten mit 64-Bit-Erfassungstransaktion. Unbekannte Altzuordnungen werden nicht geraten; explizit eigene Altintents dürfen übernommen werden. Neue generische AgentRun-Einträge erhalten WORKER und den Zeitpunkt ihrer ersten dauerhaften Anlage. Diese Metadaten bleiben bei Heartbeats unverändert. Fehlende historische Laufbelege führen zu expliziter Nichtverfügbarkeit.

Öffentliche Projektionen enthalten nur freigegebene DTO-Felder, exakte Dezimalstrings und UTC. Freie Anbieterantworten, Brokerkennungen, sensible Akteurnamen und ungeprüfte Reason-/Actiontexte werden nicht weitergereicht. Bestätigte interne Reasoncodes werden auf kanonische Kategorien abgebildet; nicht abbildbare Daten erzeugen Nichtverfügbarkeit. API-Kosten benötigen gespeicherte Tokenbeobachtung, die zugehörige ursprüngliche Audittransaktion und eine ausdrücklich konfigurierte Billingwährung. Ein unbekannter Betrag bleibt null; weder Nullkosten noch USD werden erfunden.

## Prüfungen

| Prüfung | Evidenz |
|---|---|
| Lokale kombinierte Suite | 399 passed in 5.86s |
| Transport | 21 Tests einschließlich aller18 Operation-IDs, acht Historienseiten, Negativparameter, Snapshot, endlicher ASGI-SSE-Verbindung, Headerpräzedenz, Heartbeat, Reset und Sessionwiderruf |
| PostgreSQL-Supervisor | 4 passed in 92.60s im gemeinsamen finalen Lauf |
| PostgreSQL-Regression | 10 passed in 191.27s: Migration/Restore, Jobs/Outbox, persistente HTTPkontrolle und tatsächliche Rollen-/Execution-Isolation |
| Vertrag | OAS3.1.2/18 Operationen und zehn interne Schemas unverändert valide; oasdiff gegen main586443f ohne Vertragsänderung |
| Generierter Client | Zwei65-Dateien-Läufe und vollständiger Anwendungsbaum bytegleich; TypeScript bestanden; keine generierte Datei geändert |
| Quelltext/Plan | Ruff, OpenSpec strict und40REQ/23-Task-DAG bestanden; 319 Quelltextdateien, null Credentialmuster |

Die PostgreSQL-Szenarien prüfen einen Snapshot bei parallelem Pause-/Publish-Commit, dieselben Cursor nach Service-Neuerzeugung, 100 Vorschläge ohne Dubletten oder ausgelassene Originalzeilen trotz neuem Commit, mutable Workerheartbeats, normale Orders vor erstem Report, Cursor-/Filtergrenzen, unbekannte und abgelaufene Cursor sowie konkurrierende Publisher. Die Lückensimulation wird vor Commit zurückgerollt: bestehende Publikationsguards werden nicht gelockert. Positive öffentliche Antworten werden gegen die kanonischen Schemata geprüft. Ein explizites Währungsprofil liefert unbekannte Kosten weiterhin als null; ohne Profil entsteht Nichtverfügbarkeit.

## Review und Grenzen

| Sicherheitsgrenze | Ergebnis |
|---|---|
| Order-/Credentialisolation | Keine Broker-/Executionimporte oder direkten Submitpfade in neuen Supervisor-Modulen; bestehende Gates unverändert |
| DB-Rechte | Control besitzt nur SELECT auf Provenienz und SELECT/INSERT auf Cursor; keine Berechtigung zur Änderung wirtschaftlicher Fakten oder Laufprovenienz |
| Decimal/UTC | Projektionen und tatsächliche PostgreSQL-Antworten schemakonform, keine Floatgeldwerte |
| Reconnect | Persistente opake Kennungen, explizite Ablauf-/Gapfehler, kontrollierter Streamabbruch, unabhängig von laufender DB-Abfrage terminierter Heartbeat und Deduplizierungsvertrag |
| Lokale Session | Bestehende Host-/Origin-/Cookiegrenze gilt auch für Snapshot/Stream; Session wird im laufenden Stream erneut geprüft |
| Audit/Redaktion | Bekannte Dienstaliasse, kanonische Outcome-/Reasonabbildung; keine rohen Anbietertexte oder sensiblen Akteurnamen |

Begrenzte Subagenten implementierten getrennte Dateien und führten einen ersten unabhängigen Review aus. Dieser fand fehlende Run-Provenienz, unzureichende Kontozuordnung und Audit-/Payloadabbildungen. Zwei Subagenten erreichten später ihr Nutzungslimit; ihr vollständiger Abschlussreview wird nicht behauptet. Der Hauptagent ergänzte Provenienz/64-Bit-Paginationsanker, prüfte privilegierte Trigger und Grants, korrigierte Aggregatkennungen für Policy-/Resumeereignisse und schloss die Befunde mit Sourceprüfung und tatsächlichen Tests. Eine versehentliche reine Formatierung zweier eingefrorener Migrationen wurde exakt zurückgenommen.

Anfängliche neue Testfälle deckten eine fehlende SQL-Typisierung beim ersten Cursor und fehlerhafte Audit-/Eventtyp-Fixtures auf; diese wurden korrigiert. Die gepinnte Starlette-/HTTPX-Kombination meldet weiterhin eine TestClient-DeprecationWarning. Es wurden keine Abhängigkeiten erweitert. Browser-/Deploymentzertifizierung und die vollständige Alpaca-Paperintegration bleiben die ausdrücklich getrennten nachfolgenden Aufgaben.
