# market-evidence Specification

## Purpose

Diese Fähigkeit liefert zeitlich korrekte, prüfbare Marktevidenz für einen begrenzten Paper-Durchstich und macht fehlende oder ungeeignete Daten ausdrücklich sichtbar.

## Requirements

### Requirement: REQ-MKT-001 Begrenztes Marktuniversum
Das System SHALL für den ersten Durchstich ausschließlich ein konfiguriertes kleines Instrumentenuniversum mit 15-Minuten-Kerzen verarbeiten und unbekannte Instrumente oder Intervalle zurückweisen.

#### Scenario: T-MKT-001 Unbekanntes Instrument
GIVEN ein konfiguriertes Universum und eine eingehende Kerze für ein anderes Instrument WHEN die Kerze angenommen werden soll THEN wird sie abgewiesen und nicht als Entscheidungsgrundlage verwendet.

### Requirement: REQ-MKT-002 Abgeschlossene Kerzen
Das System SHALL nur endgültig abgeschlossene Kerzen für Entscheidungs- und Risikoevidenz freigeben; laufende oder nachträglich geänderte Kerzen SHALL keine bereits getroffene Entscheidung stillschweigend verändern.

#### Scenario: T-MKT-002 Laufende Kerze
GIVEN eine noch offene 15-Minuten-Kerze WHEN ein Entscheidungsinput gebildet wird THEN bleibt die Kerze ausgeschlossen.

### Requirement: REQ-MKT-003 Normalisierte Werte und Zeit
Marktpreise und Mengen MUST als exakte Dezimalwerte und technische Zeitstempel MUST in UTC normalisiert sein; ungültige, mehrdeutige oder nicht endliche Werte SHALL zurückgewiesen werden.

#### Scenario: T-MKT-003 Ungültiger Zahlenwert
GIVEN eine Kerze mit nicht endlichem Preis WHEN sie normalisiert wird THEN wird sie als ungültig ausgewiesen und nicht freigegeben.

### Requirement: REQ-MKT-004 Datenverfügbarkeit ohne Zukunftswissen
Jede Evidenz SHALL ihren tatsächlichen Verfügbarkeitszeitpunkt tragen; Replay und laufende Entscheidungen MUST ausschließlich Evidenz verwenden, die zum jeweiligen Entscheidungszeitpunkt bereits verfügbar war.

#### Scenario: T-MKT-004 Spät veröffentlichte Evidenz
GIVEN eine nach Kerzenschluss erst später verfügbare Information WHEN ein früherer Zeitpunkt replayt wird THEN erscheint diese Information nicht im Entscheidungsinput.

### Requirement: REQ-MKT-005 Datenqualität und Lücken
Das System SHALL Frische, Vollständigkeit, Reihenfolge und Plausibilität der benötigten Marktdaten prüfen; bei Lücken, Duplikaten oder Widersprüchen MUST die Betroffenheit sichtbar und eine abhängige Freigabe gesperrt sein.

#### Scenario: T-MKT-005 Fehlende Kerze
GIVEN eine Lücke im benötigten Kerzenfenster WHEN eine Entscheidung angefordert wird THEN meldet das System fehlende Evidenz und erzeugt keine freigabefähige Handelsentscheidung.

### Requirement: REQ-MKT-006 Handelssitzung
Aktienentscheidungen SHALL nur auf abgeschlossenen Kerzen innerhalb einer ausdrücklich definierten Sitzung beruhen; Kryptowährungen MAY rund um die Uhr bewertet werden. Außerhalb der zulässigen Sitzung SHALL keine neue Aktienposition entstehen.

#### Scenario: T-MKT-006 Geschlossene Sitzung
GIVEN eine geschlossene Sitzung und die letzte gültige Kerze WHEN eine neue Freigabe angefordert wird THEN wird die veraltete Evidenz nicht für eine neue Order freigegeben.

### Requirement: REQ-MKT-007 Neutrale Evidenz
Das System SHALL beobachtete Merkmale als neutrales Evidenzbündel mit Quellen, Instrumentbezug, Aktualität, Konflikten und Qualitätsmerkmalen bereitstellen, ohne darin Ordergröße, Brokeraktion oder versteckte Handelsanweisung zu kodieren.

#### Scenario: T-MKT-007 Evidenzprüfung
GIVEN ein gültiges Kerzenfenster WHEN neutrale Evidenz erzeugt wird THEN enthält sie Herkunft und Werte, aber keine Orderanweisung.

### Requirement: REQ-MKT-008 Ausgewiesene Datenfähigkeiten
Das System SHALL pro Datenquelle und Instrument unterstützte Intervalle, Aktualität und Evidenzarten ausweisen; Marktstatus SHALL Ereignis-, Empfangs-, Verfügbarkeits- und Bezugszeit, Spread, Portfolio und Position enthalten. Nicht vorhandene News-, Paper- oder historische Fähigkeiten MUST erkennbar sein.

#### Scenario: T-MKT-008 Fehlende Fähigkeit
GIVEN eine Quelle ohne News-Daten WHEN ihre Fähigkeiten abgefragt werden THEN ist News-Evidenz als nicht verfügbar ausgewiesen.
