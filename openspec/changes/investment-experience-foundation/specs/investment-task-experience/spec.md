# Spec Delta

## Purpose

Macht persistente Researchläufe als verständliche Arbeitsaufträge mit nachvollziehbarer Absicht, begrenzten Fähigkeiten und getrennten Betreiberentscheidungen bedienbar.

## ADDED Requirements

### Requirement: IEF-A01 Persistenter versionierter Arbeitsauftrag
Ein Auftrag SHALL Betreiber, Typ, Absichtsversion, Zweck, Daten-/Toolgrenzen, Rolle, Modellbindung, Budget, Fristen, Statusversion, Evidenz, Entscheidungen, Ergebnisse und Audit referenzieren. Vorhandene Quellidentitäten und Wiederaufnahme MUST erhalten bleiben. Anlage allein MUST keinen Provideraufruf, Quantstart oder Finanzwrite bewirken.

#### Scenario: IEF-T-A01 Anlage und Reload
- **WHEN** der Nutzer denselben Analyseauftrag doppelt absendet und neu lädt
- **THEN** existiert ein Auftrag mit stabiler Quellidentität (Analyse-ID bei lokaler Analyse, Run-ID bei Research) und unveränderter Absicht; ein abweichender Inhalt unter demselben Idempotenzschlüssel wird abgelehnt.

### Requirement: IEF-A02 Tatsächliche Fähigkeiten und klare Zustände
Auftragsarten SHALL nur bei implementierter und aktuell zulässiger Capability ausführbar sein. Die Ansicht MUST tatsächliche Laufzustände in Entwurf, Rückfrage, Freigabewartezustand, geplant, laufend, abgeschlossen, blockiert, fehlgeschlagen oder abgebrochen erklären und technische Gründe zugänglich halten. Unbekannte Zustände MUST sperren.

#### Scenario: IEF-T-A02 Nicht vorhandene Marktbeobachtung
- **WHEN** der Nutzer eine nicht implementierte Markt-/Newsbeobachterrolle wählt
- **THEN** erklärt die Oberfläche die fehlende Fähigkeit und bietet einen tatsächlich vorhandenen lokalen oder Researchweg; kein Agent wird als arbeitend dargestellt.

#### Scenario: IEF-T-A03 Provider und Budget fehlen
- **WHEN** ein Lauf wegen deaktiviertem Provider oder ausgeschöpftem Budget wartet
- **THEN** zeigt die Karte den genauen Grund, benötigte Entscheidung und sichere nächste Aktion ohne automatische Aktivierung oder Budgeterhöhung.

### Requirement: IEF-A03 Eigenständige Autorisierungsentscheidungen
Auftragserstellung, Datenübertragung, Modellkosten, Hypothesenbestätigung, Quantstart und Paperorder MUST getrennte serverseitig prüfbare Entscheidungen bleiben. Jede wirkende Freigabe SHALL an konkrete Inhalte, Versionen, Akteur, Zweck und Ablauf gebunden sein. Änderungen, Widerruf und verlorene Bindung MUST weitere Wirkungen sperren.

#### Scenario: IEF-T-A04 Freier Technologieauftrag
- **WHEN** der Nutzer eine wöchentliche Erholung nach starken Technologierückgängen untersuchen möchte
- **THEN** werden Schwelle, Universum, Zeitfenster und Datenbedarf geklärt; fehlende Strategie führt zu einem Testbarkeitsblocker, nicht zu einem Ersatzexperiment. Erst gesonderte gültige Quantfreigabe erlaubt Admission.

#### Scenario: IEF-T-A05 Neue Projektion nach Freigabe
- **WHEN** Datenprojektion, Modell, Credentialgeneration, Hypothese oder Draft nach einer Vorschau wechseln
- **THEN** kann die frühere Entscheidung nicht für die veränderte Wirkung verwendet werden; die Oberfläche zeigt eine neue konkrete Vorschau.

### Requirement: IEF-A04 Sichere Wiederaufnahme und Abbruch
Auftragsansichten SHALL persistente Checkpoints, Ergebnisse und Audit nach Reload wiederfinden. Abbruch und Wiederaufnahme MUST bestehende Lease-/Fencing-/Budgetgrenzen beachten. Unbekannter externer Versand MUST ohne automatischen Wiederholungsversand und mit gebundener unbekannter Kostenreserve sichtbar bleiben.

#### Scenario: IEF-T-A06 Abbruch während Versand
- **WHEN** eine Modellanfrage bereits in flight ist und der Nutzer abbricht
- **THEN** erklärt die Ansicht die mögliche externe Wirkung, stoppt weitere zulässige Schritte und übernimmt nach Widerrufscommit kein neues Ergebnis; ein Neustart sendet nicht erneut.
