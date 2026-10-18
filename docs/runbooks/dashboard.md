# Lokaler Paper-Supervisor

Der Angular-Material-Supervisor zeigt ausschließlich den kanonischen Kontrollvertrag. Modus, Paper-Kontoalias, Verbindungen, Portfolio, Worker und Historien stammen aus dem Backend. Prozessverdrahtung und Launcher sind implementiert. Einrichtung und Anmeldung stehen in der [zentralen README](../../README.md); der Launcher startet das Fake-Lab auf `http://127.0.0.1:8000`. Der Testserver unter `tests/e2e/` ist ausschließlich eine separate synthetische Browserfixture und kein Betriebsserver.

## Ansicht und Kontrollen

Der menschliche Operator fordert den fünf Minuten gültigen einmaligen Aktivierungscode selbst in einer privaten interaktiven Konsole an; beim Start entsteht kein Code automatisch. Der genaue Befehl steht in der [README](../../README.md#schnellstart-auf-deinem-bereits-eingerichteten-rechner). Diesen Code nur in das Same-Origin-Feld „Aktivierungscode“ eingeben; niemals als URL-Parameter oder Broker-/Jev-Schlüssel verwenden. Die Oberfläche entfernt den Code unmittelbar beim Absenden. Die Session liegt als HttpOnly/SameSite-Strict-Cookie vor. Ein Seitenneuladen lädt einen neuen Backend-Snapshot; localStorage, sessionStorage und IndexedDB speichern keine Handelsdaten, Codes oder Cursor. Die Anmeldung erteilt keine Handelsfreigabe.

Die Übersicht enthält den aktuellen Backendmodus, Pausenzustand, Kill-Switch, Health und Reconciliation sowie die aktive Simulationspolicy. „Neue Einstiege pausieren“, „Fortsetzen anfordern“ und „Kill-Switch aktivieren“ verlangen jeweils eine ausdrückliche Bestätigung. Fortsetzen ist nur bei geeigneten angezeigten Voraussetzungen anwählbar; verbindlich prüft das Backend den aktuellen Zustand. Eine Ablehnung ist kein Erfolg. Der Kill-Switch wird hier nicht zurückgesetzt. Der unabhängige Kontrollpfad bleibt bei Dashboardausfall gemäß `local-operation.md` verfügbar.

Bei einem unklaren Netzwerkergebnis bleibt die Kontrollanfrage ausstehend. Eine ausdrücklich ausgelöste Wiederholung verwendet denselben Idempotency-Key. Keine automatische Mutation und keine neue Order werden ausgelöst; die UI besitzt keine Order-submit-Operation und berechnet keine Risikofreigaben.

Markt, Entscheidungen, Vorschläge, Risiko, Orders, Audit, Worker und Kosten bieten begrenzte Historienseiten. Details zeigen freigegebene strukturierte Felder statt roher Anbieterantworten. Unbekannte verschachtelte Antwortfelder werden vor dem gemeinsamen Store verworfen. Die Trace-ID verbindet die Ansicht mit dem gefilterten Auditverlauf. „Weitere Trace-Evidenz suchen“ lädt jeweils eine weitere Seite der noch nicht vollständig durchsuchten Historien; bis dahin ist die Unvollständigkeit ausdrücklich markiert. Dezimalwerte bleiben unveränderte Strings und Zeitstempel UTC. Unbekannte Kosten erscheinen als „nicht verfügbar“, nicht als Nullkosten. Confidence ist keine Gewinnwahrscheinlichkeit.

## Verbindungsstörungen

Der Supervisor startet mit Snapshot und dessen Cursor. Der separate SSE-Reader erkennt Kommentarheartbeats, wiederholt Verbindungen mit Last-Event-ID und entfernt doppelte Eventkennungen. Ein abgelaufener Cursor, `reset-required` oder eine Aggregatversionslücke lädt ausdrücklich einen neuen Snapshot. Normale Ansichtsaktualisierungen ersetzen den laufenden Streamcursor nicht. Nach 45 Sekunden ohne Heartbeat oder Ereignis wird die Anzeige als veraltet markiert; Verbindungsabbruch bleibt sichtbar. Backendverbindungen und die Verbindung der Oberfläche sind getrennte Zustände.

Bei Sitzungswiderruf werden Anzeigedaten und Cursor verworfen. Erneute Aktivierung erfordert einen neuen gültigen Code vom Launcher. Die Oberfläche zeigt nur freigegebene Fehlermeldungen und keine internen Problem-Details.

## Reproduzierbare Entwicklungsprüfung

Die bestehende verifizierte Node22.23.3-Toolchain unter `.tooling/contracts/node/` verwenden. Nach `npm ci --prefix apps/dashboard` baut `./scripts/test-dashboard.ps1 -InstallBrowser` die Produktionsoberfläche und führt die gepinnten Chromium-Browsertests aus. Mit `-SkipBuild` kann ein bereits gebauter Stand geprüft werden. Browser-/Testpakete sind exakt im Lockfile gepinnt. Der Testserver bindet ausschließlich `127.0.0.1:4200`, liefert synthetische DTOs und besitzt keine Broker-/Jev-Anbindung. Portkonflikte werden abgewiesen; ein vorhandener Server wird nicht wiederverwendet. Ausgabe liegt ignoriert unter `.tooling/output/playwright/`; Screenshot-, Trace- und Videoaufzeichnung sind ausgeschaltet.

Eine erfolgreiche Browserfixture beweist UI-/Transportverhalten, keine echte Anbieterqualität oder Paperkontoberechtigung. Die inzwischen ausgeführten Deployment-, PostgreSQL- und HTTP-Nachweise stehen getrennt in der [Gesamtabnahme](../evidence/ptv-23-final-acceptance.md); ein tatsächlicher menschlicher Paperlauf bleibt separat.
