# Design

## Context

Siehe `proposal.md` für den Nutzerumfang. Die vorhandenen Changes sind fertig, aber noch nicht in Main-Specs synchronisiert; dieser Change verändert sie nicht. Read-only Bestandsanalyse vom20.10.2026:

- `services/setup` speichert passiv. `ExecutionRuntime` liest Alpacas private Konfiguration nur bei statisch gewähltem `alpaca-paper`; der normale Launcher lehnt diese Konfiguration ab. Die Oberfläche kann den Betriebswechsel nicht selbst abschließen.
- `runtime/config.py` fixiert `lab_runtime`, internes Konto `paper`, SPY/QQQ und DISABLED als Standard. `runtime/provision.py` bindet einmalig 10000USD Fake-Cash. Diese Basis darf nicht auf den Brokerdefault umgeschrieben werden.
- Execution führt bereits Marketpublish, Bindung, Reconciliation, Recovery, Reservation und Submit aus; Worker verarbeitet dauerhafte Markt-/Decisionjobs. Diese Pfade werden wiederverwendet.
- `worker/planner.py` bindet keinen LIMIT-Preis; `risk/reservations.py` erzeugt dann MARKET. `brokers/alpaca_paper.py` lehnt MARKET-BUY korrekt ab. Nur `paper_smoke_plan.py` hat bisher eine vor Persistierung gesetzte menschliche Preisgrenze.
- `execution/broker_binding.py`, `paper_reconciliation.py` und `broker_recovery.py` besitzen die zentralen Identitäts-, Basis-, Aktivitäten-/Gebühren- und Unklarheitsgates. Die neue Bedienung darf sie nicht umgehen.
- Normales `cancel_after_halt` ist noch kein allgemeiner dauerhaft eingezäunter Cancelauftrag. Die bestehende Modusanforderung liefert teils503 trotz gespeicherter Aktivierungsanfrage; ein neuer Vorgangsvertrag muss Annahme und tatsächliches Ergebnis trennen.

## Goals / Non-Goals

**Goals:** vollständiger lokaler Bedienablauf nach persönlicher Kontoeröffnung, bestehende Execution-/Riskport-Invarianten erhalten, dauerhafte isolierte Finanzhistorie, sinnvolle Fehler und sichere Neustarts. Manuelle Ordertickets und automatischer Strategiepfad nutzen denselben Risk-/Submitpfad.

**Non-Goals:** Brokerpartner-Kontoeröffnung/KYC, Kontolöschung, Überweisungen, automatische Liquidation oder Abwicklung unbekannter Fremdorders, LIVE, Shorts, Margin, Optionen, Crypto, Extended Hours und automatische Risklockerung. Vorhandene Jev-Einrichtung bleibt erhalten; dieser Change zertifiziert keine neue Jev-Strategie.

## Decisions

### ADR-APW-001: Eigenes Paperdeployment mit fester Datenbank

Ein beim normalen Launcher vorab provisioniertes, standardmäßig inaktives Paper-Rollenpaket nutzt feste DB `lab_paper_runtime`, eigenes internes Konto `paper`, eigene Login-/SET-Rollen und getrennte Kontroll-/Marktsockets. Normales Fake-Lab und Demo bleiben vorhanden. Eine zweite Instanz der vorhandenen Runtime erhält ausdrücklich validierte feste Ledgerziele; kein frei wählbarer DB-/Host-/Shellparameter wird öffentlich angeboten.

Execution darf nach expliziter menschlicher Prüfanfrage die privaten Alpaca-Dateien öffnen und die festen Trading-/Datahosts über den bestehenden Proxy kontaktieren. Der Einrichtungsdienst bleibt passiv. Control API vermittelt nur dauerhafte Kommandos/öffentliche Projektionen. Ein eng begrenztes Paper-API-/Commandbackend besitzt ausschließlich seine fachlichen Rechte, keine Credentialmounts. Kein Dienst erhält Docker-Socket, Hostshell oder allgemeine Prozesssteuerung. Die Paperdienste warten auf persistente Kommandos und brauchen für normalen Gebrauch keinen nutzerseitigen Neustart.

Alternative: das vorhandene Fakeledger umstellen. Verworfen wegen irreversibler Basisvermischung, laufender Demoabhängigkeiten und Konfigurations-/Restartbedarf. Dynamische Container aus dem Dashboard starten ist ebenfalls verworfen, weil dies breite Hostrechte erfordern würde.

### ADR-APW-002: Einmalige Basis aus geprüfter Evidenz

Die leere Paperschemadatenbank wird vorab migriert, zunächst ohne aktive finanzielle Basis. Eine separate, eng begrenzte einmalige Initialisierungsfunktion übernimmt eine von Execution identitätsgeprüfte konsistente Evidenz unter Kontosperre: Brokerkonto, USD-Cash, erlaubte Longpositionen, exakte Kostenbasis und Marktbewertung, Aktivitätenanker, Zeit und Hash. Während Snapshotgewinnung darf keine offene Order oder wechselnde Aktivität vorliegen; vor Abschluss wird erneut gelesen und verglichen. Bei Abweichung wird keine Teilbasis freigegeben. Nicht unterstützte Positionen oder unvollständige Kostenbasis erzeugen einen erklärten Blocker.

Normale Handelsrollen erhalten keine DDL-/CREATEDB-/Rebasebefugnis. Die initiale Basis ist unveränderlich und wird bei erneutem Setup ausschließlich abgeglichen. Der Privilegienentwurf und die Reconciliation-Baseline müssen gemeinsam zertifiziert werden. Das Riskbudget ist eine zusätzliche Exposuregrenze, kein Ersatz für Broker-Cash. Ein höherer importierter Altbestand darf nicht durch ein kleineres Budget verschwinden; die UI verlangt ein verträgliches Budget oder bleibt pausiert. Es erfolgt kein automatischer Verkauf.

Alternative: nur Konten mit exakt10000USD unterstützen. Verworfen, weil dies den angeforderten Workflow unnötig auf manuelle Kontoresets zurückführen würde. Beliebige offene Fremdorders automatisch übernehmen bleibt ausgeschlossen.

### ADR-APW-003: Dauerhafte asynchrone Kommandos

Neue additive Operationen unter `/api/v1/paper/` liefern auf mutierende Anforderungen202 mit `operationId`, Idempotency-Key, akzeptierter Generation und öffentlichem Status. Die UI pollt die einzelne Operation beziehungsweise konsumiert namespacegebundene öffentliche Ereignisse; Annahme bedeutet keinen Erfolg. Feste Operationstypen: VERIFY_CONNECTION, PREPARE_LEDGER, START, PAUSE, STOP, KILL, PLACE_LIMIT_ORDER, CANCEL_ORDER. GET-Operationen lesen nur öffentliche Zustände. Kontoprüfung ist explizit read-only; kein Submit als Verbindungstest.

Kommandos werden pro Konto serialisiert, durch Lease/Generation eingezäunt und mit Audit/Outbox atomar persistiert. START bindet Konto-/Credentialgeneration, Policy-/Profilversion, Symbolmenge und Ablaufzeit. Geänderte Schlüssel invalidieren Freigaben. STOP pausiert neue Einstiege und beendet Strategieplanung; bestehende Verpflichtungen und Reconciliation laufen weiter. PAUSE und KILL erhalten unabhängig von Setup/Providerwartezeiten Priorität. Neustart setzt die Einstiegssperre. Bestehende normale API-Semantik bleibt additiv erhalten.

Schlüsselrotation aus ADR-APW-003 benötigt eine private Pendinggeneration: aktive Recoverycredentials nicht beim Speichern überschreiben. Execution prüft die Pendinggeneration auf dasselbe gepinnte Konto, schließt die Einstiegslatch und promotet erst unter Kontosperre mit erneutem Pre-Submit-Fencing. Bei offenen UNKNOWN-Verpflichtungen bleibt Promotion blockiert und der aktive private Recoverypfad erhalten. PAUSE/KILL schreiben ihre Latch unabhängig von der seriellen Kommandowarteschlange vor Bestätigung. Eine lediglich angenommene Warteschlangenanforderung darf nicht als wirksamer Kill angezeigt werden.

Importierte Longbestände aus ADR-APW-002 müssen Kostenbasis, Reconciliationstart, Exposure, Tagesverlustreferenz und Reservationen konsistent initialisieren. Die bisherigen Leer-Konto-Annahmen des Smokeablaufs dürfen nicht ungeprüft wiederverwendet werden.

Nutzerentscheidung21.10.2026: Für Paper gilt `paper-utc-equity-risk.v1`: Tagesertrag ist Eigenkapitaländerung seit belegter UTC-Tagesgrenze abzüglich nachgewiesener externer Finanzierung. Historische Centwerte erhalten als konservative Zertifizierungsregel ein Intervall von einem Cent unter bis einem Cent über dem gemeldeten Wert; Verlust wird gegen die ungünstigste Kombination und einen eingefrorenen Kapitalnenner geprüft. Unklare Transfers, fehlende Belege oder abgelaufene Referenzen sperren Einstiege. Fake-/Demoberechnung und alle bestehenden Gates bleiben erhalten. Kostenbasis wird unabhängig vom gerundeten Durchschnittspreis exakt gespeichert und fortgeschrieben.

Für ein heute nach UTC-Mitternacht angelegtes, nachweislich ungehandeltes Konto darf die erste konsistente finanzielle Bindung als ausdrücklich gekennzeichnete Ersttagsreferenz dienen: verifizierter Erstellungszeitpunkt, keine Positionen, vollständige leere Handels-/Ertragshistorie und zwei übereinstimmende Cashbelege. Die Ausnahme endet an der nächsten UTC-Tagesgrenze; danach ist ein belegter Tagesstart erforderlich. Sie behauptet keine historische Mitternachtsbilanz. Details und Quellen: `risk-basis-decision.md`.

### ADR-APW-004: Begrenzte Planung und manuelle Anforderungen

Eine versionierte Paper-LIMIT-Policy setzt vor dem unveränderlichen Vorschlag Preisgrenze, DAY, reguläre US-Session, TTL und abgerundete Menge. Preisquelle/Alter, Tickgröße und maximaler Abstand sind Teil der Policy; der Gebühren-/Reservationspuffer bleibt zusätzlich wirksam. Worker erhält ausschließlich öffentliche Markt-/Fähigkeitsevidenz, keine Credentials und keinen Submitport. SPY/QQQ bleiben die erste allowlist.

Manuelle Tickets werden als menschliche fachliche Anforderung gespeichert; weder Browser noch API greifen auf BrokerAdapter zu. Ein eigener deterministischer Planungsadapter erzeugt auditiert den vorgeschriebenen immutable Proposal-/Riskpfad ohne vorgetäuschte Modellentscheidung. Erforderliche Domainreferenzen werden ausdrücklich erweitert und typisiert, niemals mit synthetischen LLM-Entscheidungen umgangen. SELL begrenzt auf freien Longbestand; gleichzeitige manuelle/automatische Anforderungen teilen dieselbe Kontosperre und Reservierungen.

Fractional DAY-LIMIT nur für frisch bestätigte fractionable Assets und zertifizierte Kombination. Menge maximal9 Nachkommastellen, immer nach unten quantisiert; kein gleichzeitig übermitteltes notional/qty. Offizielle Quellen sind teilweise widersprüchlich: Ordertypmatrix und Fractional-Leitfaden nennen DAY-LIMIT, einzelne Referenztexte weiterhin nur MARKET. Deshalb keine Ableitung zusätzlicher Fähigkeiten aus Beispielen und kein Fallback auf MARKET; die konkrete unterstützte Kombination muss explizite Vertrags-/Adaptertests bestehen und Providerablehnungen bleiben normale sichere Fehler. Tatsächliche Providerfähigkeit bleibt bis menschlicher Prüfung unverifiziert.

Alternative: MARKET-BUY zulassen, damit der vorhandene Planner funktioniert. Verworfen wegen fehlender harter Kostengrenze. Nachträgliches Ändern von Preis/Menge ist ebenfalls ausgeschlossen.

### ADR-APW-005: Cancel und Recovery sind gleichwertige Betriebsfunktionen

Eigene dauerhafte Cancelkommandos mit owner/fence, Versuchszähler, Backoff und Audit-before-I/O ersetzen unbeschränktes tickbasiertes Wiederholen. CANCEL_PENDING ist ein echter Zwischenzustand. Reservierungen bleiben bis bestätigtem Terminalzustand bestehen; Teilfills werden genau einmal gebucht. Orders werden nach dauerhafter Client-ID und Broker-ID aufgeklärt. Timeout nach möglichem Submit führt zu UNKNOWN/Hold, niemals zum Ersatzkauf. Polling/Activities bilden zunächst die autoritative Recoveryquelle; Websocket ist für Funktion nicht erforderlich. Fremdaktivität pausiert und erzeugt einen Incident, keine automatische Korrekturorder.

### ADR-APW-006: Bedienung vollständig über Material und generierten Client

Eigener Paperbereich: Schrittfolge Konto eröffnen → Schlüssel speichern → Verbindung prüfen → Basis vorbereiten → Budget/Strategie → Paperstart bestätigen → Betrieb/Orders. Manuelle Order-/Cancelbestätigung, klare öffentliche Fehlercodes und generationgebundene Statusanzeigen. Fake-Demo bleibt separat. Keine Keys in DTOs, Status, Idempotenz, Audit, Telemetrie, Screenshots oder Browserpersistenz; bestehende flüchtige Dialogregeln bleiben gültig. HTTP-Vertrag bleibt kanonisch und Client ausschließlich generiert.

## Risks / Trade-offs

- Konsistenz während erstmaligem Broker-Snapshot → doppelte begrenzte Reads, Aktivitätenanker, Kontosperre und vollständige Abweisung bei Drift; keine Order in Vorbereitung.
- Nicht alle persönlichen Konten/Bestände sind unterstützt → konkrete fachliche Blocker vor Freigabe, keine automatische Bereinigung. Vollständiger Workflow gilt für zertifizierte US-Equity-/USD-Longfähigkeiten.
- Kostenlose Paperdaten sind IEX, Ausführung simuliert NBBO → Feedidentität und Spreads aus echter Evidenz, kein SIP-Abonnement voraussetzen, fehlende/alte Quotes schließen Gates.
- 100USD kann unter minimaler zulässiger Menge liegen → verständliche Ablehnung ohne Zwangstrade; nur bestätigte Fractionalfähigkeit nutzen.
- Keine automatisierte echte Providerabnahme → lokale Zertifizierung und menschlicher Providerlauf getrennt ausweisen. Agenten lesen keine realen Schlüssel und senden keine Providerorders.
- Umfang berührt privilegierte Initialisierung und Domain-Proposalquellen → unabhängiger Safety-/Contractreview vor jedem betroffenen Issueabschluss; Implementierung bei unerfüllten Invarianten blockiert.

## Migration Plan

1. Additiven kanonischen Vertrag, typisierte Domainanforderungen und versionierte Migrationen erstellen, gegen tatsächliches main vergleichen und Client reproduzierbar generieren.
2. Feste separate Paper-DB samt Rollen/schema leer provisionieren. Bestehende Volumes und normale Finanzbasis unverändert lassen; private Providerablage weiterverwenden.
3. Inaktive Paperrollen und bestehende gemeinsame Proxies/öffentliche API-Routen mit minimalen neuen Socket-/Netzrechten bereitstellen. Ohne Setupkommando keine Provider-I/O, ohne Start keine Orders.
4. Kontoprüfung und einmalige Basis anhand synthetischer Providerdoubles und echter SQL testen, dann Planung/Cancel/Recovery/UI integrieren.
5. Vollständige Offlineabnahme und unabhängige Reviews, Issue-Read-back und pro Issue reguläre Gitintegration. Rückfall: Paperrollen deaktivieren und nur bisheriges Fake-Lab nutzen; keine Finanzmigration rückwärts oder Datenlöschung. Paperledger bleibt zur Aufklärung erhalten.
6. Mensch meldet sich lokal an, trägt selbst Schlüssel ein und führt die sichtbare Prüffolge durch. Erfolg wird ausschließlich durch tatsächliche Providerbelege angezeigt, nicht durch den Installationsbericht.
