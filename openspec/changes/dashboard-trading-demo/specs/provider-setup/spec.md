# Spec Delta

## Purpose

Anwender richten vorhandene Alpaca-Paper-/Jev-Integrationen lokal über die Oberfläche ein und wählen exakte USD-Budgets und bestehende Entscheidungsvarianten, ohne Schlüssel offenzulegen oder Handel implizit zu aktivieren.

## ADDED Requirements

### Requirement: REQ-SETUP-001 Menschliche geschützte Schlüsseleingabe
Die Anwendung SHALL authentifizierte lokale Alpaca-Paper- und Jev-Dialoge anbieten. Schlüssel MUST ausschließlich flüchtig im Formular vorliegen, write-only über Same-Origin-POST/PUT übertragen und bei Absenden, Fehler, Abbruch und Sitzungsende gelöscht werden. Sie MUST aus Antworten, Browserpersistenz, Logs, Telemetrie, Fehlerdetails, Auditpayloads und Idempotenzspeicher ausgeschlossen bleiben.

#### Scenario: T-SETUP-001 Speichern und Wiederöffnen
- **WHEN** ein Mensch synthetische Testschlüssel einträgt und den Dialog absendet oder abbricht
- **THEN** bleiben die Eingabefelder anschließend leer und ein wiedergeöffneter Dialog zeigt ausschließlich den öffentlichen Konfigurationsstatus, niemals Schlüssel oder deren Teilstrings.

#### Scenario: T-SETUP-002 Abgewiesene Eingabe
- **WHEN** eine ungültige, zu große oder unautorisierte Einrichtungsanfrage eingeht
- **THEN** erfolgt keine Persistierung und die Antwort enthält nur einen festen redigierten Fehler ohne Eingabewerte.

### Requirement: REQ-SETUP-002 Getrennte private Ablage
Ein separater lokaler Einrichtungsdienst SHALL Schlüssel atomar mit restriktiven Besitzerrechten speichern. Dauerhafte Konsumenten MUST auf Execution für Alpaca und Worker für Jev begrenzt bleiben; Control API, Dashboard, Demo und andere Rollen dürfen keine gespeicherten Schlüssel lesen. Konfiguration SHALL klar zwischen nicht eingerichtet und gespeichert unterscheiden und MUST eine unbestätigte Verbindung nicht als verbunden darstellen.

#### Scenario: T-SETUP-003 Rollentrennung und Neustart
- **WHEN** synthetische Schlüssel gespeichert und Dienste neu gestartet werden
- **THEN** bleiben sie nur dem jeweiligen zulässigen Konsumenten verfügbar, und öffentliche Statusabfragen geben keine Credentialfelder zurück.

### Requirement: REQ-SETUP-003 Einrichtung ohne Handelsfreigabe
Alpaca-Einrichtung MUST auf den festen Paperhost und Jev auf den bestehenden festen Provider beschränkt bleiben. Speichern SHALL ohne Providerrequests, Orders oder Moduswechsel erfolgen. Eine später nötige Aktivierung beziehungsweise ein Dienstneustart SHALL verständlich angezeigt werden. Keine konfigurierbare Base-URL oder LIVE-Auswahl MAY angeboten werden.

#### Scenario: T-SETUP-004 Konfiguration bleibt passiv
- **WHEN** der Mensch Alpaca oder Jev speichert
- **THEN** bleibt der normale Handelszustand unverändert und nur „Gespeichert – Verbindung noch nicht geprüft“ wird bestätigt.

### Requirement: REQ-SETUP-004 Variable USD-Budgets
Jeder neue unabhängige Demolauf SHALL ein positives exaktes USD-Startbudget bis 1000000 akzeptieren. Ein separates Paper-Handelsbudget MUST als zusätzliche deterministische Obergrenze für gehaltenes und reserviertes Exposure wirksam sein, ohne Brokerkontostand oder Ledgerstartbasis zu verändern. Änderungen unter bestehendes Exposure oder während unklarer Orderzustände MUST abgewiesen werden. Ungültige Zahlen, Fremdwährungen und stilles Runden MUST ausgeschlossen bleiben.

#### Scenario: T-SETUP-005 Kleines Demobudget
- **WHEN** ein Nutzer einen neuen Demolauf mit 100 USD statt 100000 USD startet
- **THEN** verwenden dessen Risiko, Cash und Positionen diese unabhängige Startbasis; frühere Läufe und normale Konten bleiben unverändert.

#### Scenario: T-SETUP-006 Paper-Budgetgrenze
- **WHEN** ein Intent einschließlich offener Reservierungen das konfigurierte Paperbudget überschreiten würde
- **THEN** lehnen atomare Reservation und erneute Submitprüfung ihn ohne Orderübermittlung ab; ein kleineres Budget erzwingt keine unzulässigen Bruchteile oder Lockerung anderer Gates.

### Requirement: REQ-SETUP-005 Bestehende Entscheidungsvarianten
Die Demo SHALL die vorhandenen Mock- und deterministischen Baselinevarianten mit versionierter Auswahl anbieten. Ein Lauf SHALL Budget und Strategie unveränderlich binden. Jev SHALL separat einrichtbar sein und MUST weder Voraussetzung für die Demo noch automatischer Fallback auf ein weniger restriktives Modell sein.

#### Scenario: T-SETUP-007 Vergleichbarer Lauf
- **WHEN** zwei Läufe mit unterschiedlichen vorhandenen Varianten gestartet werden
- **THEN** sind Variante, Version, eigenes Startbudget und getrennte Ergebnisse sichtbar; ein ABSTAIN oder FLAT erzeugt keinen erzwungenen Trade.
