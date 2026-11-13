# Spec Delta

## Purpose

Kontrolliert externe Modellverarbeitung für lokale Forschung mit überprüfbarer Identität, Datenfreigabe und Kostenobergrenze.

## ADDED Requirements

### Requirement: ARP-P01 Gesperrter Providerbetrieb

Die Installation MUST den realen Modellpfad gesperrt starten. Ein versioniertes Profil MUST Provider, exakte Modellidentität, Capability, Credentialgeneration, Zweck, Datenklassen, Berechtigungen, Schema, Limits und Policy binden.

#### Scenario: Überprüfbares Verhalten

- **WHEN** Installation, Modellwechsel oder Ablauf einer Bindung
- **THEN** der Versand bleibt bis zur gültigen neuen Bindung gesperrt

### Requirement: ARP-P02 Autorisierter Transport

Der Gateway MUST ausschließlich signierte, einmalig nutzbare, aktuell gefencte und reservierte Aufträge an fest zugelassene HTTPS-Ziele senden. Redirects, frei konfigurierbare Ziele, Umgebungsproxys und Providerwechsel MUST gesperrt sein.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein gültiger Auftrag oder ein manipuliertes Ziel vorliegt
- **THEN** nur der gültige Auftrag erreicht das gebundene Ziel innerhalb der Größen- und Zeitgrenzen

### Requirement: ARP-P03 Zweckgebundene Vorschau

Der Supervisor MUST lokal klassifizieren, minimieren und sämtliche übertragenen Inhalte vor interaktivem Versand exakt anzeigen. Die einmalige Sendefreigabe MUST an Bytes, Revision, Zweck und Profil gebunden sein. Hypothesenbestätigung und Experimentfreigabe MUST separat erfolgen.

#### Scenario: Überprüfbares Verhalten

- **WHEN** Dialog, Datenrechte oder Profil nach Vorschau wechseln
- **THEN** die bisherige Freigabe kann nicht zum Versand verwendet werden

#### Scenario: Bewusste Approximation

- **WHEN** der Entwurf approximierte Testbarkeit aufweist
- **THEN** der Betreiber bestätigt die konkrete Approximation separat und revisionsgebunden. Der Forschungsmodus ändert sich dadurch nicht.

#### Scenario: Retrospektiven Forschungsmodus wählen

- **WHEN** der Betreiber RETROSPECTIVE_EXPLORATORY verwenden will
- **THEN** er wählt den Modus ausdrücklich. STRICT_PIT ist die Voreinstellung.

### Requirement: ARP-P04 Konservative Budgetführung

Vor jedem Versuch MUST atomar ein konservatives Token- und Decimal-Kostenmaximum über alle betroffenen Limits reserviert werden. Fehlende Usage oder unklarer Versand MUST als unbekannt behandelt werden. Ohne belastbaren Tarif MUST die Ausführung blockieren.

#### Scenario: Überprüfbares Verhalten

- **WHEN** Timeout oder Prozessabbruch nach möglichem Versand eintritt
- **THEN** das Maximum bleibt wirksam und der Versuch wird nicht automatisch wiederholt

### Requirement: ARP-P05 Proaktive Autorisierung

Ein proaktiver Lauf MUST einen separat aktivierten Zeitplan, gültige Zweck- und Datenrechte sowie ein aktives, hierfür erlaubtes Profil prüfen. Seine Veröffentlichung MUST nach der Antwort erneut gegen aktuelle Rechte und Evidenz geprüft werden.

#### Scenario: Überprüfbares Verhalten

- **WHEN** Zeitplan oder Profil deaktiviert oder Evidenz zurückgezogen wird
- **THEN** der blockierte Lauf ist mit serverseitigem Grund sichtbar und veröffentlicht keinen Vorschlag

#### Scenario: Proaktiver Vorschlag

- **WHEN** der Betreiber einen proaktiven Vorschlag übernehmen will
- **THEN** ausschließlich der interaktive Prüf- und Freigabeweg kann ein Experiment starten

### Requirement: ARP-P06 Begrenzter Verbindungstest

Ein ausdrücklich autorisierter Smoke-Test MUST ausschließlich feste synthetische Inhalte über dieselbe produktive Gateway-Grenze senden, ein separates Limit belasten und Modellidentität, strukturierten Output und Usage-Status prüfen.

#### Scenario: Überprüfbares Verhalten

- **WHEN** der Test erfolgreich ist
- **THEN** nur die technische Verbindung der getesteten Bindung wird bestätigt

#### Scenario: Vorbereitete Bindung

- **WHEN** ein vorbereitetes inaktives Profil einen einmaligen synthetischen Probe-Grant erhält
- **THEN** ausschließlich die Probe ist möglich. Nach Deaktivierung oder Widerruf ist auch sie gesperrt.

### Requirement: ARP-P07 Verständliche Zustände

Das Dashboard MUST Profil-, Credential-, Budget-, Lauf- und Zeitplanzustände aus Serverdaten ableiten. Providerfehler MUST eine verständliche lokale Klärungsmöglichkeit lassen.

#### Scenario: Überprüfbares Verhalten

- **WHEN** der Provider ausfällt oder das Budget erschöpft ist
- **THEN** der Grund und die lokale Alternative sind erkennbar
