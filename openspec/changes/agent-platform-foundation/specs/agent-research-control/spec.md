## Purpose

Die Control API und das Dashboard bieten konsistente Betreiberaktionen und verständliche Zustände für den gesamten Researchablauf.

## ADDED Requirements

### Requirement: APF-C01 Authentifizierte und versionsgebundene Control API
Die API SHALL paginierte, begrenzte Lesezugriffe und typisierte Schreibaktionen für Ideen, Dialog, Zeitplan, Läufe, Freigaben und Berichte anbieten. Mutationen benötigen aktuelle Betreiberautorisierung und schützen bei Wiederholung und Versionskonflikten vor doppelten oder überschriebenen Aktionen.

#### Scenario: Wiederholte Dialoganfrage
- **WHEN** derselbe Betreiber denselben Request mit gleicher Bindung wiederholt
- **THEN** erhält er dieselbe Turn-/Run-Referenz und bei geänderter Nutzlast einen Konflikt

#### Scenario: Sessionverlust
- **WHEN** ein Freigabe- oder Konfigurationsrequest ohne gültige Session oder erforderlichen Origin-/CSRF-Nachweis eingeht
- **THEN** weist ihn die API ohne Mutation ab und das Dashboard verlangt eine erneute Anmeldung

### Requirement: APF-C02 Gemeinsame Researchansicht
Das Dashboard SHALL Discovery, Dialog, Experimententwurf und Berichte über dieselben gespeicherten Versionen und bestehenden Quant-Funktionen in Guided und Advanced anzeigen. Guided erklärt Fachbegriffe und Unsicherheit. Advanced legt alle fachlichen Parameter, Manifeste, Metriken und Zeitrollen offen.

#### Scenario: Ansichtswechsel vor Freigabe
- **WHEN** der Betreiber zwischen Guided und Advanced wechselt
- **THEN** bleiben Hypothese, Approximation, Entwurf, Research Mode und Freigabebindung unverändert

#### Scenario: Nicht testbare Idee
- **WHEN** Evidenz fehlt oder eine Strategie die Hypothese nicht abbildet
- **THEN** zeigt Guided verständlich den Grund und speichert die Idee ohne Startangebot

### Requirement: APF-C03 Betreibersteuerung und Diagnose
Das Dashboard SHALL Zeitplan, Providerfähigkeit, Credentialstatus, Registry, Berechtigungen, Budget, Approval Center, Fortschritt, Toolereignisse und Berichte zugänglich machen. Es SHALL Fehler und Blockierungen mit stabilen Codes, sicheren Erklärungen und zulässigen nächsten Aktionen darstellen.

#### Scenario: Genehmigung eines Entwurfs
- **WHEN** eine konkrete Version freigegeben werden soll
- **THEN** zeigt die Oberfläche Hypothese, Annahmen, Approximation, Quellen, Strategie, Parameter, Zeitrollen, Kostenrahmen, Hash und Gültigkeit und fordert eine eigene Betreiberentscheidung

#### Scenario: Ungewisse Kosten oder Provider deaktiviert
- **WHEN** Usage unbekannt ist oder Providerrechte fehlen
- **THEN** zeigt die Oberfläche den Zustand ausdrücklich und bietet keine Umgehungsaktion an

### Requirement: APF-C04 Sichere Browserprojektion
Das Dashboard SHALL ausschließlich validierte API-Projektionen darstellen und Finanzmetriken vom Backend beziehen. Es SHALL Modelltext als untrusted Text behandeln, Zugangsschlüssel aus gespeicherten Browserzuständen ausschließen und Evidenzverweise nur über autorisierte Plattformressourcen auflösen.

#### Scenario: Eingeschleuster Link
- **WHEN** Modelltext Skript, HTML oder eine externe URL enthält
- **THEN** führt der Browser den Inhalt weder aus noch lädt er externe Ressourcen

### Requirement: APF-C05 Transparente Wahl und getrennte Entscheidungen

Das Dashboard SHALL freie Researchfragen, eine ehrliche lokale Klärung und bei zulässigem Profil die exakte Sendekontextvorschau anbieten. API und UI SHALL Sendefreigabe, fachliche Hypothesenbestätigung und Experimentstart als getrennte versionierte Betreiberaktionen behandeln. Sie MUST Ablauf, Ablehnung und Widerruf verständlich anzeigen.

#### Scenario: APF-C-T01 Bewusste Wahl je Turn
- **WHEN** ein Betreiber eine komplexe Frage eingibt und die externe Verarbeitung erwägt
- **THEN** zeigt die UI alle dynamischen Felder, den Empfänger, Zweck und Datenklassen vor der separaten Sendefreigabe und bietet jederzeit die lokale Klärung

#### Scenario: APF-C-T02 Widerruf und Reload
- **WHEN** der Betreiber die Verarbeitung ablehnt oder widerruft oder den Dialog nach Reload öffnet
- **THEN** zeigt die UI den tatsächlichen Versandstatus und gültige lokale nächste Schritte; sie erzeugt weder einen automatischen Versand noch eine fachliche Bestätigung

#### Scenario: APF-C-T03 Vorschlag bindend übernehmen
- **WHEN** der Betreiber einen Modellvorschlag mit geänderter fachlicher Bedeutung bestätigt
- **THEN** zeigt die UI die betroffenen Felder und Konflikte, prüft die aktuelle Version und speichert eine eigenständige fachliche Entscheidung ohne Quant-Start
