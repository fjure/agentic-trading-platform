## Purpose

Der Betreiber erhält überprüfbare Forschungsideen aus relevanter neuer Research-Evidenz, auch wenn das Dashboard geschlossen ist.

## ADDED Requirements

### Requirement: APF-D01 Persistenter täglicher Researchlauf
Der Supervisor SHALL einen deaktiviert ausgelieferten, persistenten Tageszeitplan mit IANA-Zeitzone, lokaler Uhrzeit, UTC-Ausführung, Pause, Aktivierung, Umfang, Budget und Publikationsobergrenze anbieten. Pro Zeitplan und lokalem Datum ist maximal ein geplanter Lauf zulässig.

#### Scenario: Sommerzeit und Neustart
- **WHEN** die konfigurierte Uhrzeit entfällt oder doppelt vorkommt
- **THEN** wird der erste gültige Zeitpunkt nach der Lücke beziehungsweise die frühere Instanz gewählt und die Entscheidung mit UTC-Zeit protokolliert

#### Scenario: Ausfall und Pause
- **WHEN** nach einem Ausfall mehrere Ausführungen fällig wären
- **THEN** holt ein aktiver Zeitplan höchstens die jüngste nach und eine Pause verhindert neue Claims und Publikationen; eine spätere Aktivierung setzt am gespeicherten Checkpoint an

### Requirement: APF-D02 Deterministischer Evidenztrigger
Der Monitor SHALL versionierte Trigger vor jeder Modellnutzung auf zulässige historische Revisionen und persistierte Researchbefunde anwenden. Er SHALL Qualität, Nutzungsrechte, Aktualität, Relevanz, frühere Forschung, Checkpoint und Budget prüfen. News, Fundamentaldaten, Makroereignisse und unbelegte Regime sind in v1 keine Evidenzquellen.

#### Scenario: Identische Evidenz
- **WHEN** derselbe kanonische Evidenzsatz bei unveränderter Auswertungsbindung erneut anliegt
- **THEN** startet weder ein weiterer Modellaufruf noch ein Experiment und der Lauf kann mit NO_NEW_RESEARCH_IDEAS erfolgreich enden

#### Scenario: Blockierte Quelle
- **WHEN** eine Revision nicht zulässig, ein Zeitbezug unklar oder das Budget erschöpft ist
- **THEN** meldet der Lauf einen stabilen Grund ohne Modellversand oder Publikation

### Requirement: APF-D03 Validierte und begrenzte Publikation
Die Pipeline SHALL nur schema- und evidenzgeprüfte Ideen innerhalb des Limits veröffentlichen. Instrument, Zeitbindung, Zitat, Herkunft, Qualität, frühere Tests und Datenrechte müssen gegen den gebundenen Kontext geprüft werden. Wiederholte oder verworfene Ideen erfordern substantiell neue begründete Evidenz.

#### Scenario: Gültige neue Evidenz
- **WHEN** ein begrenzter Modellaufruf einen belegten, neuartigen Vorschlag liefert
- **THEN** erscheint genau eine dauerhaft referenzierbare Version mit Prioritätsbegründung, Frische, Aufwand und Einschränkungen im Dashboard

#### Scenario: Unbelegte oder duplizierte Ausgabe
- **WHEN** ein Vorschlag fiktive Zahlen oder Quellen enthält, ein Duplikat ist oder die Obergrenze überschreitet
- **THEN** erscheint er nicht im Dashboard und der Lauf hält den Ablehnungsgrund fest

#### Scenario: Retraktion während des Modellaufrufs
- **WHEN** die gebundene Quelle nach Versand und vor Publikation zurückgezogen wird oder ein aktuelles Qualitätsgate fehlschlägt
- **THEN** schlägt die erneute Prüfung im Publikationscommit fehl und der Vorschlag bleibt unveröffentlicht

### Requirement: APF-D04 Reproduzierbare Neubewertung
Der Supervisor SHALL Triggerentscheidungen, gebundene Evidenz, verarbeitete Bereiche, veröffentlichte Versionen und Auswertungsfingerprints dauerhaft referenzieren. Nur eine ausdrücklich autorisierte Neubewertung mit neuer Modell- oder Promptbindung darf denselben Evidenzsatz erneut modellgestützt auswerten. Publikationsregeln bleiben dabei wirksam.

#### Scenario: Unklarer Providerzustand
- **WHEN** der Provider nach möglichem Versand ausfällt
- **THEN** bleibt der Versuch dem Evidenzsatz zugeordnet und ein späterer identischer Lauf erzeugt keinen Wiederholungsaufruf

#### Scenario: Modellwechsel
- **WHEN** nur das konfigurierte Modell wechselt
- **THEN** findet keine automatische Neuauswertung bereits verarbeiteter Evidenz statt
