# Spec Delta

## Purpose

Diese Fähigkeit stellt aus tatsächlich vorhandener Evidenz einen begrenzten Marktbezug zusammen und ermöglicht dessen kontrollierte Nutzung über API und Dashboard.

## ADDED Requirements

### Requirement: REQ-RCX-001 Verfügbarkeitsgebundener MarketContext
Ein MarketContext SHALL Instrument, Bewertungszeit, Wissensstand, Daten-/Berechnungsversionen, Qualitätsstatus, fehlende Quellen und konkrete Evidenzreferenzen enthalten. Alle Bestandteile MUST denselben Point-in-Time-Grenzen folgen. Ohne ausreichende Daten MUST der Kontext unvollständig bleiben statt Werte zu erfinden.

#### Scenario: T-RCX-001 Fehlende Quellen
WHEN nur historische Bars vorliegen THEN sind Bars und belegte Kennzahlen nutzbar und News, Fundamentals, Makro sowie Regime explizit nicht verfügbar.

#### Scenario: T-RCX-002 Zeitlich konsistente Aggregation
WHEN ein Kontext für einen früheren Wissensstand aufgebaut wird THEN enthält weder ein Kennzahlenwert noch eine Referenz später verfügbare Revisionen.

### Requirement: REQ-RCX-002 Reproduzierbare begrenzte Kennzahlen
Preisänderung und Volatilität SHALL ausschließlich aus gültigen, abgeschlossenen und nachweisbar verfügbaren Bars nach einer versionierten Berechnung entstehen. Fehlende Stichproben oder inkompatible Intervalle MUST als nicht berechenbar ausgewiesen werden; Regime oder Sentiment MUST ohne Implementierung fehlen.

#### Scenario: T-RCX-003 Zu wenig Historie
WHEN nur ein Schlusskurs oder ein unvollständiges erwartetes Fenster vorliegt THEN wird Volatilität nicht berechnet und der fehlende Datenumfang genannt.

### Requirement: REQ-RCX-003 Ehrliche Quellenfähigkeiten und getrennte Verträge
Das System SHALL getrennte normalisierte Quellenverträge für Bars, News, Fundamentals und Makro-/Kalenderereignisse führen. Der Quellenkatalog MUST implementierte, deaktivierte, blockierte und nur geplante Fähigkeiten einschließlich Historie, Feed, Lizenzstatus und Qualitätsgrenzen unterscheiden.

#### Scenario: T-RCX-004 Interface ohne Adapter
WHEN ein News- oder Makrovertrag ohne echten Adapter existiert THEN zeigt der Katalog ihn als nicht implementiert und ein Abruf liefert keine Dummy-Evidenz.

### Requirement: REQ-RCX-004 Authentifizierte begrenzte Research-API
Historische Bars, Quellen-/Qualitätsstatus, Evidenzlookup, MarketContext und Ingestionjobs SHALL über typisierte authentifizierte additive Verträge verfügbar sein. Abfragen MUST Zeitraum und Ergebnisgröße begrenzen. Ein Ingestionauftrag MUST idempotent angenommen werden und darf keine Handelsberechtigung erteilen.

#### Scenario: T-RCX-005 Authentifizierung und Eingabegrenzen
WHEN die Sitzung fehlt, der Origin fremd oder der Zeitraum zu groß ist THEN wird die Operation abgewiesen ohne Daten, Jobanlage oder Providerrequest.

#### Scenario: T-RCX-006 Wiederholter Auftrag
WHEN derselbe Ingestionauftrag mit demselben Idempotenzschlüssel erneut eingeht THEN wird derselbe dauerhafte Job referenziert; abweichende Nutzlast unter demselben Schlüssel wird zurückgewiesen.

### Requirement: REQ-RCX-005 Kleine nutzbare Researchoberfläche
Das Dashboard SHALL Instrument und Zeitraum auswählen, Historie, Quellenqualität, Evidenzreferenzen sowie Fortschritt und Fehler von Ingestionjobs anzeigen. Wissensstand und Datenlücken MUST sichtbar sein; vorhandene Fake-/Demo-/Paperfunktionen MUST erhalten bleiben.

#### Scenario: T-RCX-007 Reload und unvollständige Daten
WHEN die Oberfläche bei laufendem Job neu geladen wird THEN zeigt sie den dauerhaften Jobstatus und echte Datenlücken statt einen fingierten erfolgreichen Import.

### Requirement: REQ-RCX-006 Nachvollziehbarkeit und begrenzte Ressourcen
Ingestion und Queries SHALL korrelierbare redigierte Audit-/Metrikdaten für Dauer, Fortschritt, Retry, Qualitätsbefunde und Fehler besitzen. Anfrage-/Jobbudgets MUST den Handelsbetrieb schützen. Rohantworten, private Seitentoken und Credentials MUST aus öffentlichen Fehlern, Audit, Logs und Modellinputs ausgeschlossen bleiben.

#### Scenario: T-RCX-008 Auslastung und Ausfall
WHEN ein historischer Job sein Ressourcenbudget erschöpft oder der Provider ausfällt THEN wird er begrenzt unterbrochen und Risk, Kill, Pause sowie Reconciliation bleiben bedienbar.
