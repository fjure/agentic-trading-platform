# Spec Delta

## Purpose

Führt Menschen durch vorhandene zulässige Paper-LIMIT-Aktionen und erhält unabhängige Risikoprüfung, Execution-Isolation und sichere Klärung ungewisser Ergebnisse.

## ADDED Requirements

### Requirement: IEF-G01 Gebundene verständliche Ordervorschau
Eine Vorschau SHALL Paperkontext, Instrument, Seite, Menge, LIMIT/DAY, seitenspezifische Bruttonotionalgrenze, belegte Gebührenannahmen, verfügbare Cash-/Gewichtsauswirkungen, Datenstand und Blocker zeigen. Sie MUST unveränderlich und befristet an Konto-/Profil-/Policy-/Portfolio-/Evidenzversion gebunden sein. Fehlende Gategrundlagen MUST Bestätigung sperren; optionale unbekannte Gewichte MUST als unverfügbar erscheinen.

#### Scenario: IEF-T-G01 Vorschau ohne Wirkung
- **WHEN** der Nutzer aus Portfolio oder zulässiger Strategie eine Order vorbereitet
- **THEN** entsteht nur eine serverseitige Vorschau, keine Brokerorder oder neue Handelsfreigabe; BUY zeigt die Bruttoobergrenze, SELL den Mindestbruttoerlös. Fehlende optionale Gewichte bleiben unbekannt; fehlende vorgeschriebene Gebührenbelege sperren.

### Requirement: IEF-G02 Getrennte ausdrückliche Paperbestätigung
Nur der authentifizierte Betreiber SHALL eine konkrete gültige Vorschau ausdrücklich bestätigen können. Die Bestätigung MUST dieselben bestehenden Human-, Risk-, Reservations- und Executiongates verwenden. LIVE, Marketorders, Shorts, unbestätigte Fähigkeiten und automatische Orders aus Research MUST ausgeschlossen bleiben.

#### Scenario: IEF-T-G02 Gültige Bestätigung
- **WHEN** ein Betreiber eine frische USD-LIMIT-Vorschau bestätigt
- **THEN** wird die konkrete Anforderung idempotent angenommen; nur Execution kann nach aktueller unabhängiger Prüfung übermitteln und Annahme wird nicht als Fill angezeigt.

#### Scenario: IEF-T-G03 Veraltete Vorschau oder gehaltene BUY-Position
- **WHEN** Portfolio, Quote, Policy oder Freigabe wechseln oder ein BUY die bestehende Nachkaufsperre verletzt
- **THEN** wird der Commit erklärt abgelehnt; die UI umgeht keine Grenze und erfordert gegebenenfalls eine neue Vorschau.

#### Scenario: IEF-T-G06 Gebundener Strategieweg
- **GIVEN** der Betreiber hat eine aktuelle Paper-Eignung geprüft und die Orderdaten selbst eingegeben
- **WHEN** er Vorschau und serverseitige Risk-Vorprüfung liest
- **THEN** sind Strategieversion, Instrument, Datenstand, Eignungs- und Freigabebindungen sowie exakte Terms sichtbar bzw. referenziert. Der atomare Commit prüft die unveränderlichen, im Vorschauhash gebundenen Referenzen und den Akteur erneut. Entfernte oder manipulierte Referenzen werden abgelehnt. Bestanden ist als unverbindliche Vorprüfung gekennzeichnet. Blockiert oder nicht prüfbar sperrt die Bestätigung serverseitig. Die Vorprüfung erzeugt keine Freigabe oder Reservation. Erst die ausdrückliche Bestätigung führt in den bestehenden verbindlichen Human-/Risk-/Executionpfad. Die Oberfläche verfolgt dieselbe Operation bis zum belegten Ausführungszustand.

#### Scenario: IEF-T-G07 Änderung, Risk-Ablehnung und Abbruch
- **GIVEN** eine Vorschau ist sichtbar
- **WHEN** der Nutzer abbricht, die Risk-Vorprüfung ablehnt oder sich Strategie-, Instrument-, Daten-, Konto-, Policy- oder Freigabebindung ändert
- **THEN** entsteht keine neue Übermittlung. Die UI nennt den Grund, zeigt einen sicheren Rückweg und verlangt bei erneuter Vorbereitung eine frische Vorschau samt bewusster Bestätigung. Eine zwischen Bestätigung und Submit eintretende Sperre wird als belegte Ablehnung oder Blockade der vorhandenen Operation angezeigt.

### Requirement: IEF-G03 Nachvollziehbare unsichere Ausgänge
Die Oberfläche SHALL Anforderung, Risikoprüfung, Übermittlungsversuch, Brokerbestätigung, Teilfill, Fill, Cancel und Reconciliation unterscheiden. Doppelklick, Reload und UNKNOWN MUST dieselbe Verpflichtung erhalten. Nach unklarem Ausgang MUST eine Statusabfrage statt einer neuen Order angeboten werden; automatische Wiederholung MUST ausgeschlossen bleiben.

#### Scenario: IEF-T-G04 Antwortverlust
- **WHEN** eine Bestätigungsantwort verloren geht
- **THEN** lädt der Nutzer dieselbe gespeicherte Anforderung anhand stabiler Identität, ein erneuter Klick erzeugt keine zweite Verpflichtung und UNKNOWN hält die Reservation bis zur Klärung.

#### Scenario: IEF-T-G05 Cancel und Fill
- **WHEN** ein Fill während einer Stornierung eintrifft
- **THEN** zeigt die Ansicht den bestätigten Fill genau einmal und hält verbleibende Verpflichtungen bis zum terminalen Abgleich; Pause und Kill bleiben unabhängig erreichbar.

#### Scenario: IEF-T-G08 Wiederholung und unbekannter Ausgang
- **GIVEN** die Bestätigung könnte bereits angenommen worden sein
- **WHEN** Doppelklick, Replay, Seitenneuladen oder verlorene Antwort eintreten
- **THEN** ist höchstens eine Operation für die Vorschau möglich. Identischer Replay findet die Operation nur für den weiterhin authentifizierten und autorisierten Betreiber im selben Paperkontokontext wieder. Veränderter Replay, fremde Bindung oder abgelaufene Sitzung werden ohne Offenlegung abgelehnt. Bei UNKNOWN bleiben Reservation und Reconciliation gebunden und nur die Statusklärung wird als nächste Aktion zur Klärung angeboten.
