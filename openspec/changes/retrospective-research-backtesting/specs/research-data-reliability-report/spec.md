# Spec Delta

## Purpose

Diese Fähigkeit erklärt die Datengrundlage eines retrospektiven Experiments strukturiert und dauerhaft, damit simulierte Rendite, Datenqualität und wissenschaftliche Aussagegrenzen getrennt überprüfbar bleiben.

## ADDED Requirements

### Requirement: REQ-RRB-008 Gebundener qualitativer Bericht
Jedes retrospektive Experiment MUST einen versionierten snapshotgebundenen Bericht erhalten. Datenintegrität, historische Verfügbarkeit, Kalender, Kapitalmaßnahmen, Feedabdeckung und Nutzungsrechte SHALL getrennte qualitative Bewertungen mit Gründen tragen; Gewinn MUST keine Qualitätsbewertung verbessern.

#### Scenario: T-RRB-013 Berichtsumfang
WHEN ein Experiment geprüft wird THEN enthält sein Bericht Instrument/Quelle/Feed/Zeitraum, erwartete/vorhandene/fehlende Bars pro Fold und Warm-up, Revisionen und Datensatzzeit, Qualitätsbefunde, Ex-post-Kalender, Korrekturen, Kapitalmaßnahmenstatus, Lizenzstatus und Methodik einschließlich Parameter, Folds, Benchmarks, Kosten, Fill- und Simulationsversion. Historische Verfügbarkeit bleibt NOT_PROVEN, ohne erfundene Quote oder Reliability Score.

#### Scenario: T-RRB-014 Unbekannt und ungeprüft
WHEN Kalender fehlen oder ein Prüfbudget erschöpft wird THEN bleiben unbekannte Nenner null und die betroffenen Folgeprüfungen NOT_EVALUATED; unbekannte Sitzungen erzeugen keine erfundenen Bars und ein unvollständiger Bericht erlaubt keine Simulation.

### Requirement: REQ-RRB-009 Blockierende Integritäts- und Kalenderfehler
Fehlende erforderliche Signal-, Warm-up-, Bewertungs- oder Fillbars, widersprüchliche Revisionen, ungültige OHLCV-Werte, ungeklärte Sitzungen und gemischte Feed-/Adjustierungsreihen MUST blockieren. Das System MUST keine Lücken interpolieren oder still überspringen.

#### Scenario: T-RRB-015 Lücke und Sitzung
WHEN eine erwartete benötigte Kerze fehlt THEN bleibt der Lauf blockiert und ohne Gesamtrendite; belegte Feiertage und Earlycloses erzeugen dagegen keine künstlichen Lücken. Gemischte raw/adjusted oder IEX/SIP-Bars werden nicht zusammengeführt.

### Requirement: REQ-RRB-010 Explizite Kapitalmaßnahmen- und Preisrichtlinie
Version 1 SHALL raw price-only auswerten. Bekannte Splits, sonstige nicht modellierte strukturelle Maßnahmen, widersprüchliche Belege und verdächtige Preissprünge MUST blockieren. Unbekannte Kapitalmaßnahmen und nicht berücksichtigte Bardividenden MUST sichtbar einschränken; fehlende Belege MUST niemals Maßnahmenfreiheit behaupten.

#### Scenario: T-RRB-016 Bekannter Split
WHEN ein Snapshot einen Split im gebundenen Warm-up-/Testbereich enthält THEN bleibt jede Strategie einschließlich flat und aller Benchmarks blockiert; es entstehen weder künstliche Extremrenditen noch eine still korrigierte Stückzahl. Später bekannte Maßnahmen ändern alte Snapshots nicht, erscheinen aber als getrennte aktuelle Warnung beim Lesen.

#### Scenario: T-RRB-017 Unbekannte Maßnahmen und Dividenden
WHEN keine ausreichende Maßnahmenabdeckung vorliegt, aber alle harten Prüfungen bestehen THEN darf ausschließlich EXPLORATORY_WITH_LIMITATIONS entstehen, mit UNKNOWN statt NO_ACTIONS. Bekannte reine Bardividenden bleiben als ausgelassene Zahlungsströme sichtbar; es gibt keine Wiederanlage und keinerlei Total-Return- oder Profitabilitätsbehauptung.

#### Scenario: T-RRB-018 Verdächtiger Preisbruch
WHEN benachbarte benötigte raw-Bars eine absolute Close-zu-Open-Änderung von mindestens 20 Prozent zeigen oder ein struktureller Maßnahmenhinweis vorliegt THEN blockiert die versionierte Regel `raw-discontinuity-v1` ohne Übersteuerung; eine kleinere Änderung beweist keine Maßnahmenfreiheit. Unzureichende Vergleichsdaten bleiben gemäß Lückenregel blockiert.

### Requirement: REQ-RRB-011 Nutzungsrechte und wissenschaftliche Grenzen
Berichte MUST IEX-Teilmarkt, eingeschränkte Ausführungsrealität, Survivorship Bias des heutigen Universums, Ex-post-Revisionen und ungeprüfte damalige Veröffentlichung sichtbar nennen. Unbekannte Lizenzrechte SHALL unbekannt bleiben; explizit verweigerte Rechte MUST Simulation und Datenweitergabe blockieren.

#### Scenario: T-RRB-019 Rechte ohne Scheinnachweis
WHEN lokale bereits gespeicherte Daten unbekannte persönliche Lizenzrechte besitzen THEN erlaubt der lokale Forschungsmodus nur explorative interne Auswertung mit UNKNOWN und ohne Export/Rohdatenweitergabe oder neuen Providerabruf; eine ausdrückliche Verweigerung blockiert. Ein APIzugang oder synthetischer Test bestätigt keine Speicher-, Forschungs- oder Weitergaberechte; reale Nutzung erfordert deren private Klärung.

### Requirement: REQ-RRB-012 Vorabprüfung und beständige Reportansicht
Die Oberfläche SHALL vor ausdrücklicher Experimenterstellung eine snapshotgebundene Zulässigkeitsvorschau mit Blockern oder Einschränkungen zeigen. Abgeschlossene Reports MUST unveränderlich, paginiert und reloadbar bleiben; aktuelle Widerrufs-/Korrekturhinweise SHALL getrennt vom historischen Bericht erscheinen.

#### Scenario: T-RRB-020 Vorschaubindung und Lesefehler
WHEN ein Nutzer nach erfolgreicher Vorschau startet THEN bindet der Auftrag deren Protokoll und Snapshot; geänderte Eingaben erfordern neue Vorschau. Reportseiten bleiben an Report/Filter/Version gebunden; Sitzungsausfall, fremde Cursor und veraltete aktuelle Trustdaten erzeugen keinen positiven Status und keinen automatischen Lauf.
