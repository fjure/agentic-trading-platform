# Research-Datenzulässigkeit

## Vertragslieferung RDR-01

Der gesonderte Apply-Auftrag vom 26.10.2026 beginnt mit RDR-01/#60.
Die fünf additiven Operationen sind unter `/api/v1/research` spezifiziert:

| Methode | Pfad relativ zum Research-Präfix | Vertrag |
|---|---|---|
| POST | `/data-readiness/assessments` | Striktes bestehendes BacktestRequest, Idempotency-Key, 202; geänderte Wiederholung 409, volle Queue 429 |
| GET | `/data-readiness/assessments/{assessmentId}` | Dauerhafter Status und gebundener Bericht; unbekannte UUID 404 |
| GET | `/data-readiness/assessments/{assessmentId}/findings` | Berichtgebundene Seite, höchstens 200 Befunde; noch unveröffentlicht 409, ungültiger Cursor 400 |
| GET | `/collection/plans` | Höchstens zwei Pläne, anfänglich Version 0/DISABLED |
| PUT | `/collection/plans/{instrumentId}` | enabled/expectedVersion, Idempotency-Key; Versions-/Nutzlastkonflikt 409 |

Alle Zugriffe benötigen eine gültige lokale Sitzung und die bestehenden Origin-/CSRFgrenzen.
Der HTTPvertrag ist kein Nachweis bereits implementierter Routen. Assessor, Persistenz,
Worker, Scheduler und HTTPadapter folgen in RDR-02 bis RDR-06; die Oberfläche folgt in RDR-07.
Diese Vertragslieferung startet weder Beschaffung noch Backtests oder Handel.

Ausdrückliche Nutzerentscheidung vom 26.10.2026: zweistufige Abnahme. RDR-01 schließt mit
Vertragssuite und Clientprüfung ab. Der exakte Runtime-Routenabgleich fordert die echte
drei Diagnose-Routen automatisch bei abgeschlossenem RDR-05 und beide Plan-Routen bei
abgeschlossenem RDR-06. Alle bestehenden Routenprüfungen bleiben erhalten; diese Stufung bestätigt keine
vorhandene Runtimefunktion und ersetzt keine späteren HTTP-/SQL-/Authprüfungen.

## Interpretation der Diagnose

RDR-02 liefert jetzt den netzwerkfreien Assessor und die gemeinsamen Kalender-,
Coverage- und Qualitätsprimitive. `TrustSnapshot` bindet den aktuellen Vertrauensstand;
ein späterer Widerruf wird bei einer neuen Prüfung auch für ältere Daten berücksichtigt.
Mehrere unabhängige Blocker bleiben sichtbar. Beispielsweise bleibt ein negativer
Kapitalmaßnahmenbeleg BLOCKED, wenn der Kalender zugleich UNKNOWN ist. Lokale Aussagen
bezeichnen ausschließlich lokale Herkunft und Commit-Sichtbarkeit und bestätigen keine
historische Providerpublikation. API und dauerhafte Diagnose folgen in RDR-05.

Ausführungsstatus und Datenzulässigkeit sind getrennt. SUCCEEDED bezeichnet eine abgeschlossene
Diagnose; es kann gleichzeitig BLOCKED gelten. Die vier Zulässigkeitszustände sind ELIGIBLE,
BLOCKED, UNKNOWN und NOT_EVALUATED. Ein noch nicht gestarteter Auftrag ist NOT_EVALUATED;
Budgetabbruch bleibt unvollständig/UNKNOWN. Ein bekannter Pflichtblocker hat Vorrang vor
unbekannten oder ungeprüften Pflichtbereichen.

Integrität, Quellenauthentizität, zeitliche Authentizität, Vollständigkeit und fachliche Qualität
haben jeweils eigenes Ergebnis, Methode, Version und sichere Referenzen. Eine gültige Signatur
beweist weder historische Veröffentlichung noch Vollständigkeit oder Research Eligibility.
Aktuelle Widerrufshinweise sind eine separate Projektion; sie schreiben eingefrorene Berichte
nicht um. Ein späterer Backtest prüft seinen eigenen Snapshot und aktuellen Truststand.

Barbeginn, Barschluss, nachgewiesene Providerpublikation, vollständiger lokaler Empfang,
DB-Ingestion und lokale Commitbestätigung bleiben getrennt. Unbekannte Zeiten bleiben null
mit erklärtem Status. Commitbestätigung ist eine konservative obere Schranke und kein exakter
PostgreSQL-Commitzeitpunkt. Rechtzeitiger Empfang allein genügt nicht für Fristzulässigkeit.

Signal +60s und Outcome +300s verwenden eigene Zähler/Nenner. Erwartete eindeutige Bars kommen
aus dem damals sichtbaren regulären Kalender; fehlende Bars gehören zum Nenner. Nullnenner
bedeutet N/A. Unbekannte Kalendertage bleiben separat. Latenzquantile verwenden Nearest Rank,
mit Stichprobenzahl und unbekannten/ungültigen Zeitpaaren. Revisionen und identische Antworten
vergrößern den Barnenner nicht. Barbeobachtungsfenster und vollständige Strict-PIT-Fenster
bleiben getrennt; Warm-up, Kalender, Qualität und Kapitalmaßnahmencoverage gelten weiterhin.

## Offlinebündel

Das geschlossene Schema liegt in `contracts/schemas/research-evidence-bundle.schema.json`.
Es beschreibt normalisierte Kalender-, Qualitäts- und Kapitalmaßnahmenbelege, keine freien
Providerpayloads. Der spätere Importer muss vor dem Parsen höchstens 1MiB UTF-8 erlauben;
das Schema begrenzt auf 1000 Einträge. Freie Schlüssel, Zertifikate, Trustanker, URLs und
importeurseitige Ingestion-/Commitwerte gehören nicht in die Eingabe.

Die abgetrennte Ed25519-Signatur bindet kanonische Nutzdaten. Der Quellen-/Schlüsselregisterstand
muss unabhängig vorab zugelassen sein; Produktion beginnt leer. Die Beispiele enthalten nur
synthetische strukturelle Signaturwerte. JSON-Schema-Abnahme prüft keine Signatur, Digestgleichheit,
Zeitreihenfolge, Quellenzulassung oder historische Publikation. Diese Prüfungen und atomare
Persistenz sind Abnahmeumfang von RDR-09/RDR-03. Es gibt keinen HTTPupload.

Ohne unabhängigen Zeitnachweis bleibt zeitliche Authentizität UNKNOWN/TIME_UNPROVEN.
Ein Existenzzeitstempel beweist keine Veröffentlichung. Auch ein zugelassener unabhängiger
Publikationsbeweis verändert die tatsächliche lokale Ingestionzeit nicht und macht einen heute
importierten Beleg nicht in früheren Snapshots sichtbar.

## Sammlung und Betriebsgrenzen

Nur registrierte SPY-/QQQ-Instrumente, alpaca/iex/15Min/raw sind vorgesehen. Planaktivierung
ändert das globale Dispatchgate, Providerrechte und Handelsfreigaben nicht. Fehlende Gates
bleiben BLOCKED. enabled=false fordert eine dauerhafte Pause; deren spätere Runtimebestätigung
muss die Generation wechseln und noch offene STARTED-Abrufe ausweisen. Bereits zugelassene
Antworten behalten ihre tatsächlichen Zeiten. RDR-01 allein bestätigt keine Pausebarriere.

Sammlung ist unabhängig von einem eingerichteten Offlineimport vorgesehen. Sie erzeugt keine
Kapitalmaßnahmenbelege. Reale Providerrechte, IEX-Latenz und historische Datenzulässigkeit
bleiben unverifiziert; Standarddispatch bleibt aus. Alpaca-Credentials bleiben ausschließlich
bei Execution, LIVE bleibt unerreichbar.

## Lokale Vertragsprüfung

Aus dem Projektroot mit den gepinnten Werkzeugen:

```powershell
./.venv/Scripts/python.exe scripts/validate-contracts.py
./.venv/Scripts/python.exe scripts/check-contract-breaking.py --base-ref f829a1f53678f7e46d247a1ef6e1c5429deed154
./scripts/check-angular-client.ps1 -WriteClient
./.venv/Scripts/python.exe -m pytest tests/contracts/test_readiness_examples.py tests/contracts/test_readiness_bundle.py -q
```

`check-angular-client.ps1` erzeugt zweimal, vergleicht SHA-256 und kompiliert konkrete
Beispiele als TypeScript. Generierten Code niemals manuell ändern. Vollständige Abnahme
benötigt außerdem Redocly, Angular-Produktionsbuild, Bestandsregression, unabhängigen Review,
SourceScan und verifizierten Issue-Sync. Befunde und tatsächliche Ergebnisse werden in
`docs/evidence/rdr-01.md` festgehalten.
