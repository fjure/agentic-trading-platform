# Spec Delta

## Purpose

Der Anwender führt nach persönlicher Alpaca-Kontoeröffnung den gesamten freigegebenen USD-Paperbetrieb in der lokalen Oberfläche aus. Kontobindung, Initialisierung, Handelsfreigabe, Auftragslebenszyklus und Fehlerbehandlung bleiben nachvollziehbar und technisch von LIVE und der synthetischen Demo getrennt.

## ADDED Requirements

### Requirement: REQ-APW-001 Geführte persönliche Kontoeröffnung
Die Anwendung SHALL zur offiziellen persönlichen Alpaca-Paperkontoeröffnung führen und danach private Schlüsseleingabe anbieten. Kontoanträge, E-Mailbestätigungen und Identitätsprüfungen MUST beim Menschen und Alpaca verbleiben. Die Anwendung MUST weder personenbezogene Eröffnungsdaten erfassen noch Broker-API-Accounts erzeugen, löschen, zurücksetzen oder finanzieren.

#### Scenario: T-APW-001 Einstieg ohne Konto
- **WHEN** ein angemeldeter Mensch noch kein Alpaca-Paperkonto hat
- **THEN** zeigt die Oberfläche den offiziellen externen Einstieg und anschließend den Einrichtungsdialog; kein Account- oder Handelsrequest wird durch das bloße Öffnen ausgelöst.

### Requirement: REQ-APW-002 Explizite Kontoprüfung ohne Handelsfreigabe
Ein authentifizierter Mensch SHALL eine read-only Kontoprüfung anfordern können. Ausschließlich Execution MUST Credentials lesen und den festen Paperhost verwenden. Konto-UUID, USD, Handelsstatus, Sperren und verfügbare Marktdaten MUST geprüft werden. Gespeichert, geprüft, vorbereitet und handelsfreigegeben SHALL unterscheidbare Zustände sein; jede Prüfung bleibt ohne Order oder Modusfreigabe.

#### Scenario: T-APW-002 Gültiges Paperkonto
- **WHEN** der Mensch nach dem Speichern die Kontoprüfung startet
- **THEN** erhält er einen dauerhaften Vorgangsstatus mit redigierter Kontoidentität, USD-Beständen, Prüfzeit und eventuellen Blockern; weder Schlüssel noch vollständige private Providerantworten werden zurückgegeben.

#### Scenario: T-APW-003 Ungültige oder gewechselte Identität
- **WHEN** Credentials ungültig sind, das Konto gesperrt ist oder die erwartete UUID nicht passt
- **THEN** bleiben alle Einstiegsgates geschlossen und ein fester öffentlicher Fehler erklärt den nächsten Nutzerschritt; keine automatische Bindung an ein anderes Konto erfolgt.

### Requirement: REQ-APW-003 Geschützte Schlüsselrotation
Das System SHALL die bestehende flüchtige Same-Origin-Eingabe und private Ablage beibehalten. Eine Schlüsseländerung MUST bestehende Verbindungsbelege und noch nicht ausgeführte Freigaben invalidieren. Bei offenen oder unklaren Orders MUST ein Identitätswechsel blockieren; Recovery und Pause dürfen nicht von Jev abhängen. Kein anderer dauerhafter Dienst als Execution darf Alpaca-Schlüssel lesen.

#### Scenario: T-APW-004 Rotation während Betrieb
- **WHEN** ein Mensch die Schlüssel eines gebundenen Kontos austauschen möchte
- **THEN** pausiert der Ablauf neue Einstiege, erhält offene Verpflichtungen und verlangt erneute Identitätsprüfung; fremde Konten oder ungeklärte Orders verhindern die Umschaltung.

### Requirement: REQ-APW-004 Eigene einmalige finanzielle Basis
Das System SHALL einen isolierten dauerhaften Paperledger aus frischen, konsistenten Brokerbelegen vorbereiten. Cash, Positionen, Kostenbasis und Bewertungszeit MUST exakt übernommen und einmalig gebunden werden. Fakeledger und Demoläufe MUST unverändert bleiben. Bestehende Finanzhistorie MUST niemals überschrieben, neu basiert oder durch ein gewähltes Riskbudget ersetzt werden.

#### Scenario: T-APW-005 Verschiedene Kontostartwerte
- **WHEN** das Fake-Lab 10000 USD hat und das geprüfte Alpaca-Paperkonto 100000 USD bei gewähltem Riskbudget 100 USD
- **THEN** behält das Fake-Lab seine Basis, der neue Paperledger bindet den tatsächlichen Brokerbestand und Risk begrenzt zusätzliche Verpflichtungen auf das passende separate Budget.

#### Scenario: T-APW-006 Ungeeignete Startbelege
- **WHEN** bei Erstbindung offene Orders, nicht unterstützte Positionen, unvollständige Kostenbasis oder wechselnde Bestände vorliegen
- **THEN** entsteht keine aktive finanzielle Basis; die Oberfläche erklärt den Blocker, ohne Positionen zu verkaufen, Orders zu stornieren oder Kontodaten zu löschen.

Die Paper-Tagesverlustprüfung MUST historische Positionsgewinne von heutigem Ertrag trennen und die Eigenkapitaländerung um belegte externe Finanzierung bereinigen. Historische Centwerte MUST mit dem freigegebenen konservativen Intervall von einem Cent je Richtung bewertet werden; die ungünstigste Verlustkombination und ein eingefrorener Kapitalnenner MUST gelten. Fehlende, unklare oder abgelaufene Belege MUST Einstiegssperre auslösen. Fake-/Demoberechnung MUST unverändert bleiben.

#### Scenario: T-APW-021 Importierte Position und Tagesverlust
- **WHEN** eine erlaubte Longposition historische Buchgewinne besitzt und heute Eigenkapital verliert oder externe Finanzierung erhält
- **THEN** berücksichtigt die Paperprüfung ausschließlich den belegten heutigen Ertrag mit konservativer Genauigkeit, erhält die exakte Kostenbasis und verdeckt den Verlust weder durch historische Gewinne noch Einzahlungen.

#### Scenario: T-APW-022 Begrenzte Ersttagsreferenz
- **WHEN** ein heute nach UTC-Mitternacht angelegtes Konto anhand Erstellungszeitpunkt, fehlender Positionen, vollständiger leerer Handels-/Ertragshistorie und zwei übereinstimmender Cashbelege als ungehandelt bestätigt ist
- **THEN** darf seine erste konsistente Bindung als gekennzeichnete Ersttagsreferenz dienen; sie endet an der nächsten UTC-Tagesgrenze, danach sperrt ein fehlender belegter Tagesstart neue Einstiege.

### Requirement: REQ-APW-005 Expliziter begrenzter Paperbetrieb
Der Mensch SHALL vorbereiteten Paperbetrieb mit bestätigtem Budget, Strategie, Symbolen und Limits ausdrücklich starten können. Nur Execution MUST nach frischer Reconciliation und allen bestehenden Gates PAPER/RUNNING freigeben. Startanfragen SHALL asynchron und idempotent sein. Setup, Login, Prüfung und Neustart MUST keinen Handel aktivieren; LIVE MUST unerreichbar bleiben.

#### Scenario: T-APW-007 Start mit Bestätigung
- **WHEN** ein Mensch einen vorbereiteten Paperlauf bestätigt
- **THEN** zeigt die Oberfläche zuerst einen angenommenen Vorgang und danach dessen tatsächlichen Zustand; nur vollständig erfüllte Safetybedingungen führen zu PAPER/RUNNING.

#### Scenario: T-APW-008 Neustart oder veraltete Freigabe
- **WHEN** ein Dienst neu startet, eine Freigabe abläuft oder Konto-/Policygeneration wechselt
- **THEN** bleiben neue Einstiege pausiert; eine alte oder wiederholte Anfrage erteilt keine neue Freigabe und Recovery läuft ohne Blindresubmit.

#### Scenario: T-APW-023 Getrennte versionierte Paperkonfiguration
- **WHEN** ein Mensch im vorbereiteten Paperkontext Budget und Strategie ausdrücklich speichert
- **THEN** führen eigene authentifizierte GET-/PUT-Operationen unter `/api/v1/paper/trading-profile` eine generationgebundene, optimistisch versionierte Konfiguration mit idempotenter Bestätigung; fehlende Konfiguration erscheint mit Version0 und ohne erfundenes Budget. Jede Änderung schließt die Einstiegssperre und invalidiert frühere Freigaben. Der vorhandene Fake-Profilendpunkt bleibt unverändert.
- **AND** eine Gebührenautorität setzt die ausdrückliche menschliche Bestätigung des unterstützten direkten Retail-Paper-US-Aktienkontos voraus; sie wird separat unveränderlich und an die Profilversion gebunden gespeichert. Die ursprüngliche Anfangsbasis bleibt unverändert. Fehlende oder widersprüchliche Gebührenevidenz sperrt weiterhin.

### Requirement: REQ-APW-006 Unveränderliche preisbegrenzte Planung
Automatische und manuelle Paperaufträge SHALL unveränderlich an Symbol, Seite, Menge, LIMIT-Preis, DAY, Budget-/Policyversion, TTL und Trace gebunden sein. Sie MUST denselben deterministischen Risk-/Reservations-/Executionpfad nutzen. Preise und Mengen MUST Decimal sein. MARKET-BUY, Shorts, Margin, unbestätigte Fähigkeiten und stilles Aufrunden MUST ausgeschlossen bleiben.

#### Scenario: T-APW-009 Strategie erzeugt LONG
- **WHEN** eine vorhandene Strategie bei frischen gültigen Daten LONG liefert
- **THEN** entsteht vor Riskprüfung ein versionierter LIMIT-Vorschlag mit nach oben begrenzten Kosten; Execution verändert ihn nicht und überschreitet einschließlich Reservierungen und Gebührenpuffer kein Limit.

#### Scenario: T-APW-010 Enthaltung oder kleines Budget
- **WHEN** die Strategie FLAT ohne Position oder ABSTAIN liefert oder keine zulässige Menge in das Budget passt
- **THEN** wird keine Order erzwungen und der verständliche Grund wird angezeigt; es erfolgt weder Risklockerung noch Ersatzstrategie.

### Requirement: REQ-APW-007 Menschlicher Orderdialog
Die Oberfläche SHALL bestätigte einzelne USD-LIMIT-BUY-/SELL-Anfragen für freigegebene Symbole anbieten. Der Dialog MUST vor Bestätigung Kostenobergrenze und Paperkontext zeigen. Eine Anfrage MUST keine direkte Brokerorder sein, sondern eine dauerhafte idempotente fachliche Anforderung mit allen normalen Gates; SELL MUST auf verfügbaren Longbestand begrenzt bleiben.

#### Scenario: T-APW-011 Doppelklick und unklare Antwort
- **WHEN** ein bestätigter Auftrag wiederholt oder nach einer unterbrochenen HTTP-Antwort erneut abgefragt wird
- **THEN** ist dieselbe Anforderung mit derselben Trace-/Client-ID sichtbar; es entsteht höchstens ein Submitversuch für die ursprüngliche Verpflichtung.

#### Scenario: T-APW-012 Bruchteile
- **WHEN** ein Budget wie 100 USD nur einen Aktienbruchteil zulässt
- **THEN** wird eine abgerundete exakt darstellbare Menge nur mit frisch bestätigter passender Asset-/Orderfähigkeit geplant; andernfalls erscheint eine begründete Ablehnung ohne Order.

### Requirement: REQ-APW-008 Dauerhafte Stornierung und Kontrollpriorität
Der Mensch SHALL einzelne nicht abgeschlossene eigene Paperorders stornieren sowie neue Einstiege pausieren oder Kill auslösen können. Cancelanforderung und Versuch MUST vor I/O dauerhaft auditiert und eingezäunt sein. Pause/Kill MUST unabhängig von laufender Einrichtung wirken. Cancelantwort allein MUST weder Fillbeweis noch Reservationsfreigabe bedeuten; automatische Liquidation MUST ausgeschlossen bleiben.

#### Scenario: T-APW-013 Cancel trifft Fill
- **WHEN** während einer Stornierung ein Teilfill oder Vollfill eingeht
- **THEN** werden Fills genau einmal gebucht und die verbleibende Verpflichtung erst nach bestätigtem Terminalzustand freigegeben; die Oberfläche zeigt CANCEL_PENDING bis zur Klärung.

#### Scenario: T-APW-014 Wiederholtes Kill
- **WHEN** der Mensch Kill während laufender Netzwerk-I/O wiederholt
- **THEN** bleibt HALTED dauerhaft, keine neue Order wird angenommen, und aus wiederholten Ticks entstehen keine unbeschränkten Cancels oder automatischen Verkäufe.

### Requirement: REQ-APW-009 Fortlaufender Abgleich und Recovery
Der Paperbetrieb SHALL Orders, Aktivitäten, Gebühren, Cash und Positionen wiederholt abgleichen und persistente Cursor verwenden. Timeout nach möglichem Submit, unbekannter Zustand, externe Änderung oder fehlende Evidenz MUST Einstiegspause und fortbestehende Reservation auslösen. Wiederaufnahme MUST nach Client-/Broker-ID-Abgleich erfolgen; blindes Resubmit MUST verboten bleiben.

#### Scenario: T-APW-015 Crash nach Brokerannahme
- **WHEN** Execution nach möglicher Brokerannahme vor lokaler Antwortverarbeitung stirbt
- **THEN** sucht Recovery die bestehende Order, verarbeitet doppelte oder verspätete Ereignisse genau einmal und sendet keinen Ersatzkauf.

#### Scenario: T-APW-016 Fremdaktivität oder Rate-Limit
- **WHEN** externe Orders, unerklärte Bestandsänderungen, ein Providerlimit oder veraltete Daten erkannt werden
- **THEN** zeigt die Oberfläche den tatsächlichen Blocker und die letzte bestätigte Zeit; Backoff bleibt begrenzt und offene Verpflichtungen werden nicht als erledigt dargestellt.

### Requirement: REQ-APW-010 Sichtbarer getrennter Paperkontext
Das Dashboard SHALL einen dauerhaft markierten Alpaca-Paperbereich mit Kontoprüfung, Schritten, Budget, Strategie, Marktstatus, Reconciliation, Orders/Fills, Cash, Positionen und Tracebelegen zeigen. Datenquelle, Alter und tatsächlicher Betriebszustand MUST sichtbar sein. Jeder normale Nutzerschritt nach Kontoeröffnung MUST ohne Shell, Composebearbeitung oder privaten Dateieditor möglich sein.

#### Scenario: T-APW-017 Vollständiger UI-Ablauf
- **WHEN** ein Mensch nach Kontoeröffnung Einrichtung, Prüfung, Vorbereitung, Bestätigung, Order und Stornierung in der Oberfläche durchläuft
- **THEN** zeigen Fortschritt, endgültige Ergebnisse und spätere Seitenneuladung den gespeicherten Paperzustand; Demo- und Fakehistorien bleiben getrennt und weiterhin erreichbar.

#### Scenario: T-APW-018 Authentifizierungsverlust
- **WHEN** die Sitzung abläuft oder die Verbindung abreißt
- **THEN** werden Formulare geleert und unbestätigte Controls gesperrt; laufende Reconciliation und unabhängige Pause bleiben möglich und ein Reload behauptet keinen neuen Start.

#### Scenario: T-APW-024 Getrennte dauerhafte Paper-Leseprojektion
- **WHEN** der Mensch den Paperbereich öffnet, neu lädt oder Orders, Fills, Vorgänge und eine Tracekette nachliest
- **THEN** liefern additive authentifizierte GETs unter `/api/v1/paper/snapshot`, `/api/v1/paper/orders`, `/api/v1/paper/fills`, `/api/v1/paper/operations` und `/api/v1/paper/traces/{traceId}` ausschließlich bestätigte gespeicherte Daten des isolierten Paperledgers. Kein GET führt Provider-I/O, Credentialzugriff oder neue Handelsfreigabe aus.
- **AND** zeigen Snapshot und Detailseiten Geld/Mengen als exakte Decimalstrings, technische Zeiten in UTC, aktuelle beziehungsweise veraltete Markt-/Quote-/Capability-/Reconciliationbelege, echte Pending-/Terminalzustände und ehrliche MODEL-/HUMAN-Quellen. Fehlende Belege werden nicht durch Werte ersetzt.
- **AND** besitzen Historien begrenzte Seiten und kontextgebundene Cursor, Reload findet offene Vorgänge serverseitig wieder und listet sie vor abgeschlossenen Vorgängen, und begrenzte Traceauszüge kennzeichnen fehlende Fortsetzung ausdrücklich. Fake-/Demodaten, private Providerkonto-/Aktivitätsidentitäten, Credentials und rohe Audit-/Providerpayloads bleiben ausgeschlossen. Nur feste minimalprivilegierte Paper-Leseports vermitteln die Projektionen; keine neuen direkten Finanztabellenrechte entstehen.

### Requirement: REQ-APW-011 Reproduzierbare sichere Abnahme
Die Implementierung SHALL additive Vertrags-/Clientintegrität, reale lokale SQL-/Browserketten sowie Broker-Recovery- und Safetynegativfälle nachweisen. Entwicklungsagenten MUST ausschließlich synthetische Credentials und lokale Providerdoubles ohne externe Orders verwenden. Tatsächliche Alpaca-Funktion MUST bis zur privaten menschlichen Prüfung als unverifiziert ausgewiesen bleiben; Issue-Drift MUST den Abschluss blockieren.

#### Scenario: T-APW-019 Offline-Abnahme
- **WHEN** die vollständige Abnahme mit lokalem Providerdouble und echter Datenbank läuft
- **THEN** werden Browser→API→Execution→Order→Fill→Reconciliation einschließlich Crash/Cancel/Budget geprüft, kein erlaubter Providerhost kontaktiert und keine echten Keys gelesen.

#### Scenario: T-APW-020 Menschlicher Providerlauf
- **WHEN** der Mensch später selbst in seiner Anwendung Paper aktiviert
- **THEN** verwendet die Anwendung ausschließlich Paperhosts und normale Gates; Entwicklerbericht und UI unterscheiden lokale Zertifizierung von tatsächlich bestätigten Providerergebnissen.
