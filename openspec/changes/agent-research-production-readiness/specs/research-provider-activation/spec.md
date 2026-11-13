# Spec Delta

## Purpose

Kontrolliert externe Modellverarbeitung für lokale Forschung mit überprüfbarer Identität, Datenfreigabe und Kostenobergrenze.

## ADDED Requirements

### Requirement: ARP-P01 Gesperrter Providerbetrieb

Die Installation MUST den realen Modellpfad gesperrt starten. Ein versioniertes Profil MUST Provider, exakte Modellidentität, Capability, Credentialgeneration, Zweck, Datenklassen, Berechtigungen, Schema, Limits und Policy binden.

#### Scenario: ARP-P01-S01 – Gesperrter Neustart

- **GIVEN** eine unkonfigurierte Installation oder ein abgelaufenes Profil
- **WHEN** der Dienst neu startet und einen Modellauftrag erhält
- **THEN** kein externer Request erfolgt; ein neues Modell braucht eine neue Bindung
- **AND** der Nachweis ist [ARP-01](../../work-packages/ARP-01.md), [ARP-04](../../work-packages/ARP-04.md) zugeordnet

### Requirement: ARP-P02 Autorisierter Transport

Der Gateway MUST ausschließlich signierte, einmalig nutzbare, aktuell gefencte und reservierte Aufträge an fest zugelassene HTTPS-Ziele senden. Redirects, frei konfigurierbare Ziele, Umgebungsproxys und Providerwechsel MUST gesperrt sein.

#### Scenario: ARP-P02-S01 – Gebundener Dispatch

- **GIVEN** ein signierter einmaliger Auftrag mit aktuellem Fence und Reserve
- **WHEN** der Gateway den Auftrag annimmt
- **THEN** genau ein Request erreicht das gebundene HTTPS-Ziel
- **AND** der Nachweis ist [ARP-02](../../work-packages/ARP-02.md), [ARP-04](../../work-packages/ARP-04.md) zugeordnet

#### Scenario: ARP-P02-S02 – Ungültiger Auftrag oder Ziel

- **GIVEN** ein unsignierter Auftrag, abgelaufener Fence oder unzulässiges Ziel
- **WHEN** der Gateway den Versand prüft
- **THEN** kein Providerrequest erfolgt; Redirects werden nicht verfolgt
- **AND** der Nachweis ist [ARP-02](../../work-packages/ARP-02.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

#### Scenario: ARP-P02-S03 – Abweichende Antwortidentität

- **GIVEN** ein gültiger Auftrag mit erwarteter Modell-ID
- **WHEN** die Antwort eine andere ID meldet
- **THEN** MODEL_MISMATCH verhindert Übernahme und Modellfallback
- **AND** der Nachweis ist [ARP-02](../../work-packages/ARP-02.md) zugeordnet

#### Scenario: ARP-P02-S04 – Provider drosselt

- **GIVEN** ein autorisierter Request
- **WHEN** der Provider HTTP 429 liefert
- **THEN** RATE_LIMITED wird redigiert angezeigt, ohne Wiederholung oder Modellwechsel
- **AND** der Nachweis ist [ARP-02](../../work-packages/ARP-02.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

### Requirement: ARP-P03 Zweckgebundene Vorschau

Der Supervisor MUST lokal klassifizieren, minimieren und sämtliche übertragenen Inhalte vor interaktivem Versand exakt anzeigen. Die einmalige Sendefreigabe MUST an Bytes, Revision, Zweck und Profil gebunden sein. Hypothesenbestätigung und Experimentfreigabe MUST separat erfolgen.

#### Scenario: ARP-P03-S01 – Geänderte Versandvorschau

- **GIVEN** eine einmalige Freigabe für einen gebundenen Dialog
- **WHEN** Dialog, Datenrechte oder Profil sich ändern
- **THEN** die Freigabe verfällt und ein neuer Versand braucht eine neue Preview
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

#### Scenario: ARP-P03-S02 – Unabhängige Quantentscheidungen

- **GIVEN** eine approximierte Hypothese in STRICT_PIT
- **WHEN** der Betreiber nur die Approximation bestätigt
- **THEN** STRICT_PIT bleibt aktiv; RETROSPECTIVE_EXPLORATORY braucht eine ausdrückliche Moduswahl und Start eine eigene Freigabe
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

#### Scenario: ARP-P03-S03 – Bewusster Moduswechsel

- **GIVEN** eine Hypothese in STRICT_PIT ohne Approximationsfreigabe
- **WHEN** der Betreiber ausdrücklich RETROSPECTIVE_EXPLORATORY wählt
- **THEN** der Modus wechselt. Eine erforderliche Approximation bleibt gesondert freizugeben.
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

### Requirement: ARP-P04 Konservative Budgetführung

Vor jedem Versuch MUST atomar ein konservatives Token- und Decimal-Kostenmaximum über alle betroffenen Limits reserviert werden. Fehlende Usage oder unklarer Versand MUST als unbekannt behandelt werden. Ohne belastbaren Tarif MUST die Ausführung blockieren.

#### Scenario: ARP-P04-S01 – Konkurrierende Reservierungen

- **GIVEN** zwei Versuche, deren Einzelreserven passen und deren Summe das gemeinsame Limit überschreitet
- **WHEN** beide gleichzeitig reservieren
- **THEN** höchstens einer erhält eine vollständige Reserve und Sendeberechtigung
- **AND** ein ausgeschöpftes gemeinsames Limit führt für den unterlegenen Versuch zu BUDGET_EXHAUSTED
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

#### Scenario: ARP-P04-S02 – Timeout oder fehlende Usage

- **GIVEN** ein Versuch nach möglichem Sendestart
- **WHEN** ein Timeout eintritt oder Usage fehlt beziehungsweise unvollständig ist
- **THEN** OUTCOME_UNKNOWN hält die konservative Reserve, ohne automatischen Retry
- **AND** die öffentliche Fehlerprojektion verwendet PROVIDER_OUTCOME_UNKNOWN
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

#### Scenario: ARP-P04-S03 – Unvollständiger oder veralteter Tarif

- **GIVEN** ein Profil ohne gültigen Tarif für eine erreichbare Komponente
- **WHEN** eine Reserve angefordert wird
- **THEN** die Anforderung bleibt ohne Versand gesperrt
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P04-S04 – Budgetrevision oder UTC-Wechsel

- **GIVEN** ein Attempt mit Preview und ursprünglicher UTC-Periode
- **WHEN** vor Commit die Budgetrevision oder der UTC-Tag wechselt
- **THEN** aktuelle Limits werden geprüft; eine geänderte Bindung braucht neue Freigabe; Settlement früherer Attempts bleibt in ihrer Periode
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P04-S05 – Belegte Kostenüberschreitung

- **GIVEN** ein abgeschickter Attempt
- **WHEN** verifizierte Kosten die Reserve überschreiten
- **THEN** Ledger erfasst den Überlauf und sperrt weiteren Dispatch
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

### Requirement: ARP-P05 Proaktive Autorisierung

Ein proaktiver Lauf MUST einen separat aktivierten Zeitplan, gültige Zweck- und Datenrechte sowie ein aktives, hierfür erlaubtes Profil prüfen. Seine Veröffentlichung MUST nach der Antwort erneut gegen aktuelle Rechte und Evidenz geprüft werden.

#### Scenario: ARP-P05-S01 – Widerrufene Discovery-Rechte

- **GIVEN** ein proaktiver Lauf mit laufender Anfrage
- **WHEN** Profil, Zeitplan oder Evidenzrecht vor Publikation widerrufen wird
- **THEN** der Vorschlag bleibt unveröffentlicht und der Sperrgrund erscheint serverseitig
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

#### Scenario: ARP-P05-S02 – Interaktive Übernahme

- **GIVEN** ein publizierter Discovery-Vorschlag
- **WHEN** der Betreiber ihn übernimmt
- **THEN** nur die interaktive Prüf- und Freigabekette kann ein Experiment starten
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-07](../../work-packages/ARP-07.md) zugeordnet

### Requirement: ARP-P06 Begrenzter Verbindungstest

Ein ausdrücklich autorisierter Smoke-Test MUST ausschließlich feste synthetische Inhalte über dieselbe produktive Gateway-Grenze senden, ein separates Limit belasten und Modellidentität, strukturierten Output und Usage-Status prüfen. Die Probe MUST eine belegte vollständige modellgebundene Kostenobergrenze atomar im hierarchischen Ledger gegen das konfigurierte Betreiberlimit reservieren.

#### Scenario: ARP-P06-S01 – Günstiges und teureres Modell

- **GIVEN** zwei Modellbindungen mit belegten unterschiedlich hohen Maximalbeträgen innerhalb der jeweiligen Betreiber- und Ledgerlimits
- **WHEN** jede Bindung ihren eigenen einmaligen Smoke-Grant erhält
- **THEN** je ein synthetischer Roundtrip reserviert seinen Modellbetrag; passende ID, Schema und vollständige Usage bestätigen ausschließlich die Verbindung
- **AND** der Nachweis ist [ARP-08](../../work-packages/ARP-08.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-P06-S02 – Vorbereitete Bindung und fehlende Freigabe

- **GIVEN** ein STAGED-Profil ohne gültigen Probe-Grant
- **WHEN** ein Smoke ausgelöst wird
- **THEN** es erfolgt kein Versand; ein separater Grant erlaubt ausschließlich eine synthetische Probe, DISABLED und REVOKED sperren auch diese
- **AND** der Nachweis ist [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P06-S03 – Fehlende obere Schranke

- **GIVEN** eine Tokenzählung ohne harte Grenze oder Reasoning ohne belegtes Limit
- **WHEN** die Probe vorbereitet wird
- **THEN** eine dokumentierte vollständige Obergrenze gilt als Fallback; ohne diese bleibt der Versuch mit Sperrgrund ungesendet
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P06-S04 – Reasoning und Cachekomponenten

- **GIVEN** ein Profil mit abrechenbaren Reasoning- und Cachetokens
- **WHEN** der Maximalbetrag berechnet wird
- **THEN** alle erreichbaren Komponenten werden gemäß Tarif vollständig und ohne Doppelzählung begrenzt und reserviert
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P06-S05 – Limit unter Maximalbetrag

- **GIVEN** eine Probe mit belegter Reserve oberhalb des gesetzten Betreiberlimits
- **WHEN** der Betreiber die Vorschau öffnet
- **THEN** Maximalbetrag und Sperrgrund sind sichtbar, ohne Versand
- **AND** der Nachweis ist [ARP-07](../../work-packages/ARP-07.md), [ARP-08](../../work-packages/ARP-08.md) zugeordnet

#### Scenario: ARP-P06-S06 – Veralteter oder wiederholter Attempt

- **GIVEN** ein alter Tarif, eine veraltete Modellbindung oder ein bereits versandter Attempt
- **WHEN** der Gateway einen Versand dafür erhält
- **THEN** kein neuer externer Request erfolgt
- **AND** der Nachweis ist [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

#### Scenario: ARP-P06-S07 – Entzug vor oder während Probe

- **GIVEN** ein gebundener Smoke-Attempt
- **WHEN** Widerruf vor Send-Commit oder während des Requests eintritt
- **THEN** vor Commit bleibt er ungesendet; nach Commit bleibt unklare Usage belastet und eine späte Antwort ist nur zur Kostenklärung nutzbar
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md), [ARP-04](../../work-packages/ARP-04.md), [ARP-08](../../work-packages/ARP-08.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

#### Scenario: ARP-P06-S08 – Antwort ohne verifizierbare Verbindung

- **GIVEN** ein einmalig gesendeter Smoke-Request
- **WHEN** die Modellidentität abweicht oder abrechnungsrelevante Usage fehlt
- **THEN** der Smoke bleibt unbestätigt; unbekannte Kosten bleiben konservativ belastet
- **AND** bei MODEL_MISMATCH mit vollständiger verifizierbarer Usage wird der ursprüngliche Versuch abgerechnet. Bei unvollständiger Usage bleibt die Reserve bestehen.
- **AND** der Nachweis ist [ARP-08](../../work-packages/ARP-08.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

### Requirement: ARP-P07 Verständliche Zustände

Das Dashboard MUST Profil-, Credential-, Budget-, Lauf- und Zeitplanzustände aus Serverdaten ableiten. Providerfehler MUST eine verständliche lokale Klärungsmöglichkeit lassen.

#### Scenario: ARP-P07-S01 – Diagnose mit lokaler Alternative

- **GIVEN** eine verfügbare lokale UI bei Providerfehler oder erschöpftem Budget
- **WHEN** der Betreiber die Seite neu lädt
- **THEN** serverseitiger Sperrgrund, getrennte Freigabestatus und lokale Klärung erscheinen ohne Neuversand
- **AND** der Nachweis ist [ARP-07](../../work-packages/ARP-07.md) zugeordnet
