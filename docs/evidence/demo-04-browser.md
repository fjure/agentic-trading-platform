# DEMO-04 tatsächliche Browserabnahme

Abnahmeharness: `scripts/test-demo-browser.ps1` baut das echte Angular-Dashboard und
startet Chromium gegen `http://127.0.0.1:4210`. Die Oberfläche spricht über den
bestehenden unveränderten STDIO-/HTTP-Bridgepfad mit der kanonischen FastAPI,
dem tatsächlichen DemoCoordinator und PostgreSQL. Keine API-Antwortfixtures,
Browser-Routenmocks oder fertigen Order-/Portfoliowerte werden eingespeist.

Der Harness erzeugt ausschließlich UUID-Testressourcen: einen temporären
DB-Login, eine Metadatenbank, eine getrennte normale Testledgerdatenbank und
rungebundene vollständig migrierte finanzielle Datenbanken. Der Browser nutzt
eine eigene echte LocalAuth-Sitzung; der kurzlebige synthetische Aktivierungscode
bleibt im Prozessspeicher. Die normale Benutzeranwendung auf Port 8000 wird
weder angesprochen noch gestoppt. Die persistenten Demoresultate werden vor dem
gezielten Cleanup zusätzlich unmittelbar per SQL geprüft.

Geprüft werden zwei Browserstarts (100 USD/mock-neutral und 200 USD/neutral-baseline),
je eine FILLED-Order, SPY-Menge 1, Cash 95 beziehungsweise 195 USD, gemeinsame
Trace-IDs der Order-/Decision-/Risk-/Auditkette und dieselbe Order nach Reload.
Ein 0,01-USD-Lauf wird durch echte deterministische Riskprüfung ohne Brokerannahme
abgewiesen und bleibt pausiert. Finanzielle Tabellen, Risk-/Pause-/Killzustand,
Policy und Profile des getrennten normalen Testledgers werden vor/nach den
Browseraktionen gehasht und müssen identisch bleiben.

Alpaca-/Jev-Dialoge speichern ausschließlich synthetische Canaries über die echte
Control API, den FixedSetupClient und den separaten lokalen Einrichtungsdienst
in flüchtige private Testverzeichnisse. Der Test prüft leere Dialogfelder nach
Wiederöffnung, redigierte Antworten und keine Browserpersistenz. Screenshots,
Traces, Videos und DOM-Fehlersnapshots sind abgeschaltet; Fehlerartefakte werden
nicht aufbewahrt und Testausgaben vor Ausgabe um die synthetischen Eingaben redigiert.
Keine echten Providercredentials, Anbieterrequests oder Orders.

Reproduktion (laufendes lokales PostgreSQL und vorhandenes Smoke-Image erforderlich):

```powershell
./scripts/test-demo-browser.ps1
# Optional nur bei fehlendem gepinntem Chromium:
./scripts/test-demo-browser.ps1 -InstallBrowser
```

Ausgeführt am20.10.2026: **1 echter Chromiumtest bestanden,21,6s**. Zusätzlicher SQL-Nachweis:3unabhängige Läufe,2gefüllte Orders,95/195USD Cash,0,01USD-Riskablehnung pausiert und normales Testledger unverändert. Der gesamte Wrapper inklusive Cleanup endete erfolgreich. Ein vorheriger erfolgreicher Browser-/SQL-Durchlauf endete erst bei der abschließenden Windows-Konsolenausgabe mit UnicodeEncodeError; ASCII-Abschlussmeldung korrigiert und gesamten Ablauf erneut erfolgreich ausgeführt.
