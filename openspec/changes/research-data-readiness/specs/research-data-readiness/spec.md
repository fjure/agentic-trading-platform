# Spec Delta

## Purpose

Diese Fähigkeit erklärt die belegte Datenzulässigkeit eines konkreten Backtestprotokolls und bewahrt überprüfbare Beleggrundlagen, ohne historische Verfügbarkeit oder Handelsfreigaben zu erfinden.

## ADDED Requirements

### Requirement: REQ-RDR-001 Gebundene Zulässigkeitsprüfung
Ein Bericht SHALL ein gültiges Backtestprotokoll, Regel-/Codeversion und unveränderlichen Datensnapshot binden. Er MUST dieselben Kalender-, Warm-up-, Signal-, Ergebnis- und Qualitätsgrenzen wie der Backtest anwenden. Er SHALL Datenzulässigkeit getrennt von Ausführungsstatus und Providerrechten ausweisen; eine Simulation oder Beschaffung MUST unterbleiben.

#### Scenario: T-RDR-001 Parität mit Backtest
WHEN ein Protokoll auf demselben Snapshot diagnostiziert und materialisiert wird THEN stimmen Datenzulässigkeit und Blockierursachen für alle Folds überein; Signalwissen bleibt Barende plus 60 Sekunden, Ergebnisbeobachtung Barende plus 300 Sekunden und Gebühren-/Fillannahmen bleiben unverändert.

#### Scenario: T-RDR-002 Historischer Backfill
WHEN eine alte Kerze oder ihr Pflichtbeleg erst nach dem maßgeblichen Wissenszeitpunkt beobachtet wurde THEN weist der Bericht die verspätete Beobachtung aus und bleibt für diesen Zeitpunkt blockiert; ein älteres Ereignisdatum oder ein nachträglicher Import hebt die Grenze nicht auf.

### Requirement: REQ-RDR-002 Vollständige begrenzte Lückenerklärung
Berichte SHALL unabhängig prüfbare Lücken pro Fold mit Kategorie, Intervall, Wissenszeitpunkt und sicheren Belegreferenzen ausweisen. Unbekannte Kalender MUST von fehlenden Bars unterschieden werden. Nicht auswertbare Folgeprüfungen MUST als ungeprüft erscheinen. Fehlende Pflichtprüfung oder erschöpftes Budget MUST einen positiven Gesamtstatus verhindern.

#### Scenario: T-RDR-003 Mehrere Blocker und unbekannter Kalender
WHEN Kalender und Kapitalmaßnahmenbelege fehlen und andere Teilbereiche prüfbar sind THEN erscheinen beide unabhängigen Blocker sowie die übrigen prüfbaren Befunde; unbekannte Sitzungen erzeugen keine erfundenen fehlenden Bars, Feiertage gelten nur mit gültigem Kalenderbeleg.

#### Scenario: T-RDR-004 Begrenzung und stabile Seiten
WHEN Befund- oder Laufbudgets erschöpft werden THEN bleibt der Bericht ausdrücklich unvollständig und niemals ELIGIBLE; Seiten sind stabil an Bericht und Filter gebunden, und ein fremder oder manipulierter Cursor wird abgewiesen.

### Requirement: REQ-RDR-003 Kontrollierter Offlinebelegimport
Ein lokaler Offlineimport SHALL streng typisierte Kalender-, Qualitäts- und Kapitalmaßnahmenbelege mit Instrument, Quelle, Geltungsbereich, tatsächlicher Beobachtung, Revision und Fingerprint prüfen. Nur registrierte Quellen mit dokumentierter zulässiger Nutzung MUST akzeptiert werden. Die Herkunft MUST gegen vorab gepinnte öffentliche Quellenschlüssel geprüft werden. Freie URLs, ausführbarer Inhalt und rückdatierte Ingestion MUST abgewiesen werden.

#### Scenario: T-RDR-005 Wiederholung und ungültiges Bündel
WHEN dasselbe gültige Belegbündel zweimal importiert wird THEN entsteht höchstens ein bestätigter Import mit identischen Referenzen; ungültige Zeitbeziehungen, unbekannte Felder, Quellen oder Instrumente und konfliktbehaftete Identitäten erzeugen keine Teilpersistenz.

#### Scenario: T-RDR-006 Keine Beweise durch Behauptung
WHEN ein Benutzer nur ein früheres Datum, eine eigene Bestätigung, einen eigenen Fingerprint oder das Fehlen sichtbarer Preissprünge als Beleg angibt THEN wird daraus weder historische Verfügbarkeit noch Kapitalmaßnahmenfreiheit abgeleitet; eine fehlende/manipulierte Signatur, ein unbekannter/abgelaufener/widerrufener Schlüssel oder ein selbst mitgelieferter Vertrauensanker wird abgewiesen; ein heute akzeptierter Beleg bleibt für frühere Wissensstände unsichtbar.

### Requirement: REQ-RDR-004 Unveränderliche und widersprüchliche Evidenz
Belege und abgeschlossene Berichte MUST unveränderlich bleiben. Korrekturen SHALL neue zeitlich gebundene Revisionen erzeugen. Sichtbare negative oder widersprüchliche Kapitalmaßnahmenbelege MUST positive ältere Belege sperren. Eine Qualitätsauflösung MUST den betroffenen Bereich explizit abdecken; fehlende Daten MUST unbekannt bleiben.

#### Scenario: T-RDR-007 Korrektur und negative Evidenz
WHEN nach einem Bericht ein widersprechender Beleg eingeht THEN bleiben alter Bericht und früherer Snapshot unverändert, während eine neue Prüfung den Konflikt berücksichtigt; ein älterer positiver Beleg kann ihn nicht umgehen.

#### Scenario: T-RDR-008 Teilweise Qualitätsauflösung
WHEN ein neuer gültiger Qualitätsbeleg nur einen Teil eines verworfenen Bereichs abdeckt THEN bleibt der ursprüngliche Reject gemäß bestehender quality_at-Regel vollständig ungelöst, bis ein einzelner späterer VALID-Beleg ihn ganz abdeckt; leere Providerantworten bestätigen weder Kalendercoverage noch Kapitalmaßnahmenfreiheit.

### Requirement: REQ-RDR-005 Dauerhafte authentifizierte Bedienung
Berichtserstellung, Detail und Befundseiten SHALL über additive authentifizierte Verträge verfügbar sein. Idempotente Wiederholung MUST denselben Auftrag liefern; geänderte Nutzlast bei gleichem Schlüssel MUST scheitern. Die Oberfläche SHALL nach Reload Status, Snapshot, Grenzen und Abhilfe zeigen; ein positiver Bericht MUST keine Backtest- oder Handelsfreigabe sein.

#### Scenario: T-RDR-009 Sitzung und Idempotenz
WHEN Sitzung oder Origin unzulässig sind THEN entsteht kein Bericht; zulässige Wiederholung erhält dieselbe ID, geänderte Nutzlast liefert Konflikt, und nach Reload bleibt der persistierte Bericht mit seinen Befunden lesbar.

#### Scenario: T-RDR-010 Veraltete Diagnose
WHEN nach einer Diagnose weitere Evidenz eingeht oder ein Backtest beauftragt wird THEN ist der Bericht weiterhin als snapshotgebunden sichtbar; der Backtest bindet und prüft seinen eigenen Snapshot und akzeptiert die Diagnose nicht als Freigabetoken.

### Requirement: REQ-RDR-006 Isolation und tatsächliche Abnahme
Diagnostik MUST ohne Provider-, Broker-, Jev- oder Credentialzugriff laufen und nur eigene Berichte schreiben. Der Offlineimport MUST auf Belegschreibrechte beschränkt sein. Alle Pfade MUST Handelsledger, Riskgates und Betriebsfreigaben unverändert lassen; LIVE MUST unerreichbar bleiben. Prüfungen SHALL synthetische Evidenz von realen Providerrechten trennen.

#### Scenario: T-RDR-011 Tatsächliche Rechte und Last
WHEN Diagnose und Import unter Last und mit fehlerhaften Eingaben geprüft werden THEN sind Finanzschreibrechte und private Mounts unzugänglich, keine externen Aufrufe aus der Diagnose möglich und Pause/Kill/Reconciliation weiterhin verfügbar; ein abgelaufener Jobinhaber kann keine Ergebnisse veröffentlichen.

#### Scenario: T-RDR-012 Ehrlicher Offlineabschluss
WHEN die Offlineabnahme besteht THEN belegt sie Softwareverhalten einschließlich erfolgreicher und blockierter synthetischer Fenster; persönliche Nutzungsrechte, reale historische Nutzbarkeit und eine Provideraktivierung bleiben gesondert auszuweisen.

### Requirement: REQ-RDR-007 Getrennte Vertrauensdimensionen
Evidenz SHALL Integrität, Quellenauthentizität, zeitliche Authentizität, Vollständigkeit und fachliche Qualität getrennt mit Status, Methode, Regelversion und Referenzen ausweisen. Research Eligibility MUST daraus protokoll- und snapshotgebunden nach gemeinsamen Backtestregeln entstehen. Eine gültige Signatur oder hohe Empfangsquote allein MUST niemals ELIGIBLE begründen. Unabhängig unbewiesene historische Zeitangaben MUST ausdrücklich unbekannt bleiben.

#### Scenario: T-RDR-013 Signatur ohne Zeitbeweis
WHEN ein unverändertes Bündel mit gültiger zugelassener Herkunftssignatur nur einen selbst behaupteten früheren Publikationszeitpunkt enthält THEN sind Integrität und Herkunft prüfbar, zeitliche Authentizität bleibt UNKNOWN mit Grund TIME_UNPROVEN; ein selbst ausgestelltes Zertifikat oder eigener Zeitstempel ist kein unabhängiger Nachweis und der Datensatz wird dadurch nicht ELIGIBLE.

#### Scenario: T-RDR-014 Unabhängige Zeitbindung ohne Rückdatierung
WHEN ein zugelassenes unabhängiges Zeitnachweisverfahren Inhalt, Revision, Quelle und nachgewiesene öffentliche Verfügbarkeit bindet THEN wird nur diese präzise Aussage als verifiziert ausgewiesen; ein bloßer Existenzzeitstempel genügt nicht als Publikationsbeweis, und der heute erfolgte lokale Import bleibt für damalige Strict-PIT-Snapshots unsichtbar.

#### Scenario: T-RDR-015 Fehlende Kette und fachlich falscher Inhalt
WHEN eine Beweiskette fehlt, der Quellengeltungsbereich nicht passt oder ein korrekt signierter Datensatz unplausible OHLC-/Intervallwerte beziehungsweise Lücken enthält THEN bleiben die betroffenen Vertrauens-, Qualitäts- und Vollständigkeitsdimensionen unabhängig UNKNOWN oder REJECTED; Research Eligibility folgt der Aggregation und ist bei bekanntem Pflichtblocker BLOCKED. Kryptografische Integrität überschreibt keinen dieser Befunde.

### Requirement: REQ-RDR-008 Versionierte Vertrauensprüfung und ehrliche Status
Quellen-/Schlüsselentscheidungen und Rücknahmen SHALL append-only versioniert mit Wirksamkeit, Prüfzeit, Geltungsbereich und Grund erhalten bleiben. Neue Prüfungen MUST sichtbaren Widerruf, Kompromittierung und Rücknahme berücksichtigen; eingefrorene Berichte MUST erhalten bleiben. ELIGIBLE MUST vollständig positive Pflichtprüfungen voraussetzen; BLOCKED, UNKNOWN und NOT_EVALUATED MUST unterscheidbar sein.

#### Scenario: T-RDR-016 Widerruf und Revision
WHEN ein Schlüssel oder Beleg nach einem Bericht widerrufen, kompromittiert oder zurückgezogen wird THEN bindet eine neue Prüfung die aktuelle Vertrauensversion und sperrt dessen positive Verwendung; der alte Bericht bleibt reproduzierbar als damalige Aussage erhalten und wird mit separatem aktuellen Warnhinweis angezeigt. Rücknahme einer Rücknahme erzeugt eine neue Entscheidung und keine Löschung.

#### Scenario: T-RDR-017 Vollständige Statusaggregation
WHEN eine Pflichtprüfung scheitert THEN ist die Gesamtzulässigkeit BLOCKED; ohne Blocker, aber mit unbekanntem Pflichtbeleg ist sie UNKNOWN; ohne Blocker oder Unbekanntes, aber mit ungeprüfter Pflichtprüfung ist sie NOT_EVALUATED. Nur vollständig positive Pflichtprüfungen ergeben ELIGIBLE. Ein noch nicht gestarteter Auftrag ist NOT_EVALUATED; ein Budgetabbruch ist unvollständig/UNKNOWN, niemals erfolgreich zulässig.

#### Scenario: T-RDR-018 Unabhängiger Persistenzabschluss
WHEN Persistenz und Vorwärtsbeobachtung ohne Trusted Evidence Import installiert und mit lokalen HTTPdoubles geprüft werden THEN entstehen unveränderliche Revisionen, ehrliche Zeiten und wiederholbare Diagnosebefunde; fehlende externe Vertrauensbelege bleiben blockierend. Die spätere Importinstallation verändert keine alten Revisionen und besitzt keine Runtime- oder Finanzschreibrechte.
