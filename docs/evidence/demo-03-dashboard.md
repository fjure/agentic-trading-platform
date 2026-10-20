# DEMO-03: sichtbare Demo und Einrichtung

Stand20.10.2026. Angular-Material-Supervisor enthält nach Anmeldung den Abschnitt „Demo & Einrichtung“ mit variablem exaktem USD-Startbudget, den vorhandenen Mock-/Baselinevarianten, laufendem Status, persistenter Runhistorie und Order-/Cash-/Positions-/Decision-/Risk-/Auditdarstellung. Alle Werte stammen vom generierten Client und echten API-Projektionen; kein eingebautes Erfolgsfixture ersetzt den Handelsablauf.

Alpaca-/Jev-Dialoge verwenden kurzlebige Formulare; Eingaben werden vor Abschluss der HTTP-Anfrage zurückgesetzt und beim Schließen/Destroy verworfen. Einrichtungsstatus enthält ausschließlich CONFIGURED/UNCONFIGURED und Neustartbedarf. Schlüssel werden weder zurückgegeben noch in Browserpersistenz geschrieben; Speichern aktiviert keinen Handel. Paperbudget und Strategie werden über den getrennten Profilvertrag gespeichert.

Angularproduktionsbuild bestanden; zwei reproduzierbare88-Dateien-Clientgenerierungen mit TypeScriptprüfung bestanden. Zwei zusätzliche Playwright-Fixturetests bestanden; die separate tatsächliche Browser→API→PostgreSQL-Abnahme hat am20.10.2026 ebenfalls bestanden und nach Reload dieselbe Order, Cash95/195USD, Position1 und Tracebelege nachgewiesen. Synthetische Alpaca-/Jev-Canaries durchlaufen dort den tatsächlichen Einrichtungsdienst; keine echten Schlüssel oder Providerrequests.

Unabhängiger begrenzter Sicherheitsreview20.10.2026 ohne verbleibenden bestätigten P1/P2. Bedienung und Grenzen in `docs/runbooks/dashboard-demo.md` und README dokumentiert. Keine Aussage zu Strategierentabilität oder einer tatsächlich geprüften Providerverbindung.
