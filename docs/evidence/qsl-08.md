# QSL-08 Material Strategy Lab

Issue #84. Der Research-Bereich öffnet das Quant Strategy Lab bei Bedarf oder
über einen gespeicherten Experiment-, Batch- oder Reliability-Link. Die
Oberfläche verwendet Angular Material und den generierten Research-Client.

Der Builder zeigt den registrierten Katalog, das Kandidatenraster, die zwei
Referenzrollen, Phasen und die vorab gewählte Policy. Gültige Budget- und
Startbelege stammen aus der API. Beim Öffnen eines gespeicherten Entwurfs
lädt der Builder dessen vollständige Konfiguration. Eine Änderung entwertet
die angezeigte Validierung und erzeugt beim Speichern eine neue Version.

Die Ergebnisansicht trennt Rollen, Phasen, Nichtwahl und technische
Teilfehler. Sie zeigt exakte Auswahlwerte, serverseitige Aktivitäts- und
Expositionswerte sowie Research-Historie und aktuelle Reliability-Warnungen.
Kurven verwenden die gelieferten Punkte. Abfragen binden Kandidat, Rolle,
Phase und Fold. Beim Sitzungsverlust werden Abfragen abgebrochen, Polls
gestoppt und verspätete Antworten verworfen. Permanente Links enthalten
Ressourcen-IDs.

## Ausgeführte Prüfungen

- Die strikte Angular-Templateprüfung und der unabhängige UI-Nachreview sind
  grün. Der Review bestätigte die Korrekturen für identische Cursorparameter,
  doppelte Seitenabrufe, Kennzahlen vor Veröffentlichung der Kurven und die
  Formularwerte beim Wechsel gespeicherter Entwürfe.
- `npm --prefix apps/dashboard run build` erzeugte den Produktionsbuild. Das
  Initialbundle misst 978,45 kB. Der verzögert geladene Quant-Teil misst
  72,88 kB. Die vorhandenen Bundle- und Supervisor-CSS-Warnungen blieben
  sichtbar.
- Der Chromium-Lauf über den Produktionsbuild, authentifizierte HTTP-API,
  den Worker und isoliertes PostgreSQL bestand mit **2 Tests in 2,2 Minuten**.
  Das echte Strict-PIT-Raster aus neun EMA-Kandidaten und zwei Referenzen
  reservierte elf Slots. Der Batch erreichte `SUCCEEDED` nach 102 Sekunden
  bei 2.750 Barschritten und 2.042.656 Publikationsbytes. Die Browserfixture
  verwendet kurze synthetische Handelssitzungen.
- Der Browser zeigte dokumentierte Nichtwahl, `NO_EXPOSURE`, eine negative
  Buy-and-Hold-Referenzrendite durch Gebühren, getrennte Phasen, die
  Validation-Sensitivität, serverseitige Kurven und paginierte Ergebnisse.
  Der gespeicherte Reliability-Link öffnete denselben Bericht nach Reload.
  Ein Folgebatch mit verzögerter echter Polling-Antwort bestätigte, dass ein
  Sitzungsverlust die Antwort verwirft und weitere Quant-Abfragen stoppt.
  Die abschließende SQL-Prüfung fand null Brokerorders.
- Auf demselben Produktionsbuild bestanden die bestehenden Strict- und
  Retrospective-Browserregressionen mit **4 Tests in 24,2 s** beziehungsweise
  **8 Tests in 54,6 s**. Beide Harnesses bestätigten gespeicherte Zustände und
  null Orders oder Providerzugriffe in ihren isolierten Datenbanken.
