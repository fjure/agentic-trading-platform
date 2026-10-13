# Spec Delta

## Purpose

Diese Fähigkeit begrenzt jeden Handelsversuch durch deterministische Freigabe, atomare Risikoreservierung und prüfbare Paper-Ausführung auch bei Zeitüberschreitungen und Abstürzen.

## ADDED Requirements

### Requirement: REQ-RSK-001 Sichere Betriebsmodi
Das System MUST ausschließlich DISABLED, REPLAY, SHADOW und PAPER erlauben und in DISABLED starten. LIVE SHALL weder konfigurierbar noch über einen anderen Pfad erreichbar sein; REPLAY und SHADOW MUST jede Broker-Orderübermittlung ausschließen.

#### Scenario: T-RSK-001 Verbotener Modus
GIVEN eine Konfiguration mit LIVE WHEN sie geladen wird THEN wird sie verworfen und keine Orderübermittlung aktiviert.

### Requirement: REQ-RSK-002 Kontrollierter Übergang zu PAPER
Ein Wechsel zu PAPER MUST ausdrücklich angefordert, auditiert und nur nach erfolgreicher Gesundheitsprüfung und Broker-Reconciliation erlaubt sein. Nach unsicherem Neustart MUST mindestens PAUSE_NEW gelten, bis beide Prüfungen erneut erfolgreich sind.

#### Scenario: T-RSK-002 Unsicherer Neustart
GIVEN ein zuvor aktiver PAPER-Modus WHEN der Prozess nach unklarem Abbruch startet THEN bleiben neue Einstiege pausiert, bis Health und Reconciliation erfolgreich sind.

### Requirement: REQ-RSK-003 Begrenzte Zielpositionen
Das System SHALL nur LONG und FLAT als Zielpositionen zulassen und Shorts, Optionen, Futures, Hebelprodukte und echte Geldbewegungen ausschließen. Broker SHALL Orderarten, Bruchteile, Paper-Modus und Sessions als Fähigkeiten ausweisen; nicht bestätigte Fähigkeiten MUST als nicht verfügbar gelten.

#### Scenario: T-RSK-003 Nicht bestätigte Fähigkeit
GIVEN ein Vorschlag für eine Short-Position oder eine nicht bestätigte Fähigkeit WHEN Risiko prüft THEN wird der Vorschlag abgelehnt.

### Requirement: REQ-RSK-004 Deterministische Risikofreigabe
Jede Paper-Order MUST genau ein unveränderliches TradeProposal und eine deterministische RiskDecision referenzieren. Ohne gültige Marktevidenz, Portfoliozustand, Brokerfähigkeit und vollständige Risk-Policy MUST die Freigabe scheitern.

#### Scenario: T-RSK-004 Fehlende Evidenz
GIVEN ein TradeProposal mit veralteter Marktevidenz WHEN Risiko entscheidet THEN lautet die Entscheidung Ablehnung mit stabilem Reason Code und es entsteht keine Order.

### Requirement: REQ-RSK-005 Gültigkeit und erneute Prüfung
Ein TradeProposal SHALL kurz befristet sein; unmittelbar vor jeder Übermittlung MUST Gültigkeit, Modus, Pausezustand, Risk-Policy und verfügbare Evidenz erneut geprüft werden. Eine frühere Freigabe SHALL eine spätere Sperre nicht übergehen.

#### Scenario: T-RSK-005 Ablauf zwischen Freigabe und Submit
GIVEN ein freigegebener Vorschlag WHEN seine Gültigkeit vor der Übermittlung abläuft THEN wird die Übermittlung gesperrt.

### Requirement: REQ-RSK-006 Versionierte Paper-Limits
Die Risk-Policy SHALL validierte, versionierte und auditierte Limits für Positionen, Notional und Tagesverlust enthalten. Simulationsdefaults SHALL höchstens eine Position je Instrument, zwei insgesamt, 10 % Paper-Equity je Position und 1 % Tagesverlust erlauben; diese Werte MUST als Simulation gekennzeichnet sein.

#### Scenario: T-RSK-006 Erreichtes Verlustlimit
GIVEN das konfigurierte Paper-Tagesverlustlimit ist erreicht WHEN ein neuer Einstieg geprüft wird THEN wird er abgelehnt und die wirksame Policy-Version ist nachvollziehbar.

### Requirement: REQ-RSK-007 Atomare Budgetreservierung
Die Risikoprüfung MUST bestehende Positionen, offene Orders und Reservierungen einbeziehen und Budget für konkurrierende Vorschläge atomar reservieren. Parallele Vorgänge SHALL dasselbe Budget niemals doppelt verbrauchen.

#### Scenario: T-RSK-007 Parallele Vorschläge
GIVEN zwei gleichzeitig geprüfte Vorschläge überschreiten zusammen das freie Limit WHEN beide reservieren wollen THEN erhält höchstens einer die benötigte Reservierung.

### Requirement: REQ-RSK-008 Ausschließliche Paper-Ausführung
Nur die getrennte Execution-Grenze MAY nach gültiger RiskDecision eine Order an ein einziges verifiziertes Paper-Konto übermitteln; Agenten, Entscheidung und Control-Oberfläche MUST ohne direkten Broker-Submit bleiben. Ein nicht ausdrücklich aktivierter Paper-Smoke-Pfad MUST vor jeder Übermittlung abbrechen.

#### Scenario: T-RSK-008 Unberechtigter Submit
GIVEN ein Agentenlauf oder ein nicht aktivierter Smoke-Test WHEN eine Orderübermittlung versucht wird THEN wird sie ohne Brokerkontakt verweigert.

### Requirement: REQ-RSK-009 Unbekannter Orderstatus und Reconciliation
Nach Submit-Timeout oder unklarem Brokerstatus MUST die Order UNKNOWN und ihre Reservierung bestehen bleiben. Vor einem Retry MUST Reconciliation über interne und brokerseitige Kennungen erfolgen; Ledger-Abweichungen SHALL neue Einstiege und Positionsvergrößerungen sperren.

#### Scenario: T-RSK-009 Timeout nach Submit
GIVEN ein Submit endet ohne bestätigten Brokerstatus WHEN derselbe Auftrag erneut verarbeitet wird THEN erfolgt zuerst Reconciliation und kein blinder Zweit-Submit.

### Requirement: REQ-RSK-010 Orderzustände und Rennen
Das Orderledger SHALL CREATED, RISK_RESERVED, SUBMITTING, ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, REJECTED, CANCEL_PENDING, CANCELED und UNKNOWN unterscheiden. Doppelte oder ungeordnete Ereignisse sowie Cancel/Fill-Rennen MUST idempotent zu einem nachvollziehbaren Zustand führen.

#### Scenario: T-RSK-010 Fill während Cancel
GIVEN eine Order ist CANCEL_PENDING WHEN eine Teilfüllung und eine Stornobestätigung ungeordnet eintreffen THEN werden Füllmenge und Endstatus korrekt erhalten, ohne doppelte Positionsänderung.
