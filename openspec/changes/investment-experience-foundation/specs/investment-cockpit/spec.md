# Spec Delta

## Purpose

Verbindet Portfolio, verständliche Researchergebnisse, offene Entscheidungen und sichere Paperaktionen zu einem konsistenten lokal authentifizierten Investment-Cockpit.

## ADDED Requirements

### Requirement: IEF-C01 Kohärente Navigation und Dashboard
Das Cockpit SHALL Portfolio, Risiko, KI-Erkenntnisse und Hauptaktionen priorisieren und alle zehn Hauptansichten konsistent verbinden. Fachdetails MUST per Progressive Disclosure erreichbar sein. Bestehende Deep Links, Advanced- und Betriebspfade MUST erreichbar bleiben; Datenqualität und Demo-/Paperkontext MUST dauerhaft sichtbar sein.

#### Scenario: IEF-T-C01 Wiederkehrender Betreiber
- **WHEN** der Betreiber nach Login das Cockpit öffnet
- **THEN** sieht er bestätigte Portfoliodaten, offene Entscheidungen und aktuelle Aufträge mit Quelle und Datenstand sowie Einstieg zu Analyse, Strategien, Auftrag, Paper und Verlauf.

#### Scenario: IEF-T-C02 Alter Researchlink
- **WHEN** ein bestehender Ideen-, Approval-, Bericht- oder Quantlink geöffnet wird
- **THEN** wird dessen ursprüngliche Identität im passenden neuen Kontext angezeigt, ohne Start, Freigabe oder Herkunftsverlust.

### Requirement: IEF-C02 Ehrliche Zustände und zugängliche Bedienung
Alle Hauptansichten SHALL deutsche verständliche Texte, Loading-, Leer-, Fehler-, Offline-, Konflikt- und Recoveryzustände besitzen. Sie MUST bei Desktop, Tablet und Mobile sowie per Tastatur und Screenreader bedienbar sein. Status MUST zusätzlich zu Farbe textlich unterscheidbar sein; fehlende Daten MUST ohne erfundene Beispielwerte erscheinen.

#### Scenario: IEF-T-C03 Mobile Fehlerbehandlung
- **WHEN** bei 360 Pixel Breite die Sitzung oder Datenverbindung ausfällt
- **THEN** bleiben Datenalter und Blocker lesbar, der Fokus erreicht die sichere Folgeaktion, keine primäre Aktion erfordert horizontales Scrollen und Wiederanmeldung startet keine Wirkung.

### Requirement: IEF-C03 Quellengebundene Activity Timeline
Die Timeline SHALL gespeicherte Aufträge, Entscheidungen, Research- und Paperereignisse mit Akteur, UTC-Zeit, Kontext, Quellidentität, Version und Detailverweis projizieren. Pagination und Reconnect MUST Lücken und Duplikate verhindern oder einen neuen Snapshot verlangen. Die Ansicht MUST begrenzte redigierte Evidenz statt Geheimnissen oder internen Gedankengängen liefern.

#### Scenario: IEF-T-C04 Wiederverbinden
- **WHEN** der Nutzer nach Streamunterbrechung oder abgelaufenem Cursor zurückkehrt
- **THEN** sieht er vollständige Fortsetzung oder eine erklärte Snapshotneuladung; Paperfills und Researchfreigaben werden nicht doppelt als neue Aktionen dargestellt.

### Requirement: IEF-C04 Reproduzierbare integrierte Abnahme
Die sechs Nutzerreisen SHALL mit Produktionsfrontend, Control API, temporärem PostgreSQL, echten Research-/Quantkomponenten und synthetischen Provider-/Executiondoubles verbindlich geprüft werden. Fehlende ARP-Readiness MUST produktive Researchbereitschaft und integrierten Abschluss blockieren. Offline-Evidenz MUST von echten Betreiber-/Providernachweisen getrennt bleiben.

#### Scenario: IEF-T-C05 Getrennte Nachweise
- **WHEN** die Offline-Journeys erfolgreich sind, aber ARP-10 oder ein benötigter Betreiberbeleg fehlt
- **THEN** werden nur tatsächlich bestandene Offline-Prüfungen ausgewiesen und die betroffene produktive Freigabe bleibt offen; Tests senden keine echten Provider- oder Brokerrequests.
