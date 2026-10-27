# RDR-04: Execution-eigene Vorwärtsbeobachtung

Stand: 27.10.2026. Ausschließlich synthetische lokale HTTP-Doubles und isolierte
PostgreSQL-Testdatenbanken; keine echten Providerabrufe oder Orders.

Die dauerhafte Sammlung bindet Instrument, Generation, Zweck und Fälligkeit mit
eindeutigen Schlüsseln. Ein interner Kalenderzweck verwendet die bestehenden
normalisierten Kalenderports; Barjobs verwenden den bestehenden UPDATEpfad mit
30 Minuten Überlappung. Schedulertransaktionen enden vor Provider-I/O. Das globale
Gate bleibt standardmäßig aus; Sammlung funktioniert ohne Offlineimportregister.

Die Zulassung schreibt STARTED unter demselben Planlock wie Pause. Nur aktuelle
Generation, aktueller Besitzer und Fence mit gültiger Lease können einen neuen
Abruf zulassen. Bereits zugelassene Antworten dürfen nach Pause mit tatsächlichen
Zeiten persistieren. Nach Crash wird der alte Versuch UNKNOWN; Wiederaufnahme
benötigt einen neuen Fence. Ein abgeschlossener Versuch ist nicht wiederverwendbar.

Vollständiger HTTP-Empfang wird vor Normalisierung gemessen. Ereignisbeginn,
Barschluss, unbekannte Publikation, Empfang, DB-Ingestion und separat bestätigte
Commits bleiben getrennt. Bestandsbarrevisionen werden bei identischen Antworten
nicht neu datiert. Empfangsreceipts sind pro Versuch; öffentliche Projektionen
verbinden sie mit der ursprünglichen Inhaltsrevision. Uhrvergleichbarkeit wird
ohne Nachweis nicht behauptet. Die gemeinsame Assessor-Suite prüft Fristgrenzen,
Nenner, Duplikate, ungültige Zeitpaare und getrennte Signal-/Outcomefenster.

## Ausgeführte Prüfungen

- Reale PG-/HTTP-Prüfung: zwei tatsächliche Schedulerprozesse erzeugen höchstens
  zwei offene logische Jobs. Ein zweiter Prozess pausiert während eines laufenden
  HTTP-Doubles. Nach bestätigter Pause entstehen keine neuen STARTED-Receipts;
  die laufende Antwort behält Empfang, tatsächliche Ingestion und Commitbestätigung.
- Reale PG-Recoveryprüfung: 72 Stunden Ausfall hinterlassen eine sichtbare ältere
  Lücke; nur 24 Stunden werden nachgeholt. Neustart dedupliziert bestehende Jobs.
  Tagesbudget 100 bleibt über Generationen wirksam. Staler Fence, falscher Besitzer,
  abgelaufene Lease und erneute Zulassung abgeschlossener Kalender werden abgewiesen.
- Reale PG-Sicherheitsprüfung: Control, Worker und Diagnoseworker können keine
  Beobachtungsreceipts schreiben; Execution kann keine zertifizierte Evidenz
  schreiben. Deaktivierung führt zu keiner Factory und keinem GET. Safety wird nach
  Factoryerstellung erneut geprüft. HTTP429 erzeugt UNKNOWN und dauerhaften Retry;
  lokale Researchsperre lässt bestehende Safetyreads weiterhin zu.
- `tests/integration/test_research_collection.py` und
  `tests/security/test_research_collection_isolation.py`: **2 bestanden, 133,19s**
  nach Fencekorrektur. Recovery inklusive neuer Fencefälle: **1 bestanden, 68,18s**.
- `tests/unit/test_research_collection.py` und `test_research_limits.py`:
  **21 bestanden, 0,59s**. Enthalten DST-Kalendergrenzen und Empfangsmessung vor
  künstlich um 55 Sekunden verzögerter Normalisierung.

Die Tests nutzen den bestehenden isolierten PG-/Restorehost. Testidentitäten
werden flüchtig über stdin übertragen. Finanzrechte, Produktionsdaten und laufende
Dienste werden nicht verändert. Kein Datenbanklock wird über HTTP gehalten.

## Grenzen

Reale Providerrechte, Uhrsynchronisierung und Providerlatenzen sind unverifiziert.
Das Verfahren garantiert keine +60s-/+300s-Fristen. Plan-HTTPsteuerung und öffentliche
Projektionen werden in RDR-06, die gemeinsame Last-/Browser-/Safetykette in RDR-08
abgenommen. Diese Grenzen ersetzen keine individuelle Strict-PIT-Prüfung.

Unabhängiger Safetyreview abgeschlossen: Der zunächst gefundene veraltete
Fence wurde korrigiert und mit tatsächlichem PG-Recoverytest geprüft. Auch ein
vor Dispatch bekannter Credential-/Rechteblocker wird dauerhaft sichtbar. Die
erweiterte Sicherheitsprüfung bestand erneut in 66,55s. Keine offenen P1/P2
im geprüften Sammlungsumfang. Ruff ist für alle Paketdateien fehlerfrei.
