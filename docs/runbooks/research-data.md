# Researchdaten und historische Ingestion

Der Change `market-data-and-research-foundation` befindet sich im Apply. Die hier
beschriebenen lokalen Implementierungen sind erst nach vollständiger Verifikation
und Issueintegration abgenommen. Private Alpaca-Datenrechte sind **UNVERIFIED**.

## Grenzen und Betrieb

Die bestehende Paper-Execution besitzt den historischen GET-Adapter und konsumiert
die vorhandene geschützte Credentialgeneration. Die öffentliche Control-API reicht
Researchanfragen ausschließlich an die vorhandene private `paper-api:8012` weiter.
Diese liest gespeicherte Researchdaten und legt dauerhafte Requests an. Worker,
Dashboard und öffentliche API erhalten keine Alpaca-Credentials. Research aktiviert
weder Paperhandel noch LIVE und erzeugt keine Orderintents oder Brokerorders.

Standard ist `research_ingestion_enabled=false`. Ein Start registriert lediglich
die versionierten Researchidentitäten und den nicht geheimen Quellenstatus. Dabei
wird weder eine Credentialgeneration gelesen noch ein Providerclient geöffnet.
Researchinitialisierung läuft unabhängig von Command-, Cancel- und Recoverytakten;
ein Fehler lässt diese Sicherheitsschleifen weiterlaufen.

Die vorhandene nicht geheime Paper-Runtimekonfiguration versteht zusätzlich:

| Feld | Standard | Bedeutung |
| --- | --- | --- |
| `research_ingestion_enabled` | `false` | Expliziter Researchdispatch nur in Paperdeployment |
| `research_license_confirmed` | `false` | Menschlich bestätigte persönliche Datenrechte |
| `research_policy_version` | `null` | Version der lokal bestätigten Datenpolicy, bei Bestätigung erforderlich |
| `research_get_limit` | `null` | Bestätigtes gemeinsames GET-Limit pro Minute; unbekannt sperrt Research |

Diese Felder enthalten keine Zugangsdaten. Die Einrichtung bestehender Alpaca-
Credentials bleibt beim separaten authentifizierten Einrichtungsdienst. Ein
erfolgreicher Offlinefixturetest bestätigt keine persönlichen Providerrechte.

## Instrumente und Datenbedeutung

`packages/research/registry.py` definiert stabile, vom Handelsledger getrennte
SPY-/QQQ-Research-UUIDs. Instrumente und Symbolgültigkeiten sind append-only.
SPY ist ab 1993-01-29, die Schreibweise QQQ erst ab 2011-03-23 registriert.
Frühere QQQQ-Historie wird nicht automatisch auf QQQ umgeschrieben. Quellen:
[SPY-Emittentenunterlage](https://www.sec.gov/Archives/edgar/data/1222333/000119312513023294/d473476dfwp.htm),
[QQQ-Tickersupplement](https://www.sec.gov/Archives/edgar/data/1067839/000110465912078569/a12-27371_2497.htm).
Die registrierte MIC beschreibt das Instrument und beweist keine historische
Handelsplatzzuordnung. Eine Symbolgültigkeit beweist auch keine Providercoverage.

Unterstützt sind ausschließlich SPY/QQQ, USD, `iex`, `raw`, `15Min` und `1Day`.
IEX ist kein konsolidierter Gesamtmarktfeed. Es gibt keinen SIP-Fallback und keine
nachträglich erfundenen Splitfaktoren. Tagesbars werden dem regulären New-York-
Sessionbeginn zugeordnet; ihre Providerbucket-Mitternacht ist kein Handelsbeginn.
Preise und Mengen bleiben Decimalstrings, technische Zeitstempel UTC.

Ohne belegte historische Veröffentlichungszeit gilt `FIRST_OBSERVED`: Verfügbarkeit
ist die von PostgreSQL gesetzte Ingestionzeit. Heute importierte alte Bars gelten
dadurch nicht rückwirkend als damals bekannt. Jede Korrektur erzeugt eine neue
unveränderliche Revision mit Vorgänger. A → B → A erzeugt drei Vorkommen, von denen
das erste und dritte denselben Inhaltsfingerprint haben. Unmittelbare Wiederholung
des aktuellen Vorkommens erzeugt keinen weiteren Datensatz.

## Abfragen, Kalender und Kontext

Abfragen binden Bewertungszeit, Wissensstand, Snapshotzeit und committed Datenversion.
Ein opaker Cursor ist zusätzlich an alle Filter gebunden; Filterwechsel oder ein
Cursor nach Prozessneustart erfordern eine neue erste Seite. Die Versionszeile wird
erst für kurze SQL-Schreibtransaktionen gesperrt, niemals während Provider-I/O.
Uncommittete ältere Versionsreservierungen gelangen nicht nachträglich in einen
bereits gebundenen Snapshot. Evidenz-ID-Lookups verwenden dieselben PIT-Grenzen.

Nur gemeinsam erhaltene, persistierte Kalenderantworten können innerhalb ihrer
beobachteten Sessiongrenzen eine ausgelassene Datumslücke als geschlossen belegen.
Leere Antworten, Antwortgrenzen und voneinander getrennte Antworten beweisen keine
Feiertage. DST und frühe Schlüsse stammen aus versionierten New-York-Sessions.
Unbekannte Bereiche bleiben `UNKNOWN_CALENDAR`; IEX-Lücken heißen `NO_OBSERVATION`.
Es werden keine synthetischen Bars ergänzt und HTTP 200 verspricht keine Vollständigkeit.

Verworfene Antworten und Revisionskonflikte bleiben als unveränderliche normalisierte
Qualitätsbefunde mit betroffenem Zeitraum erhalten. Rohwerte verworfener Antworten
werden nicht gespeichert. Erst ein vollständig erfolgreicher Lauf über den gesamten
betroffenen Bereich kann den aktuellen Befund auflösen; alte Wissensstände behalten
ihn. Qualitätsbefunde verändern keine ursprüngliche Barrevision.

Contextwerte referenzieren ausschließlich Evidenz derselben Sicht. Preisänderung
und Populationvolatilität einfacher Schlusskursrenditen verwenden Decimalpräzision38,
HALF_EVEN und18Nachkommastellen. Mindestens drei gültige Bars sowie das vollständige
erwartete Fenster sind erforderlich. Ein wegen Ergebnislimit abgeschnittenes Fenster
bleibt nicht berechenbar. News, Fundamentals, Makro und Regime bleiben ohne echten
Adapter `NOT_IMPLEMENTED`; Fixture-Normalisierer erzeugen keine Produktionsdaten.

## Jobs, Limits und Wiederaufnahme

Ein idempotenter Auftrag legt zuerst einen dauerhaften Job an. Gleicher Schlüssel
und gleiche Nutzlast referenzieren denselben Job; andere Nutzlast ergibt409.
Deaktivierter Dispatch oder unbestätigte Rechte ergeben dauerhaft `BLOCKED`, ohne
Provider-I/O. Die GET-Jobansichten starten keine Arbeit.

Es existiert höchstens eine aktive Researchlease. Seitenwerte, Evidenz, Receipt,
privater Cursor und Fortschritt committen gemeinsam. Leaseübernahme erhöht den
Fence; ein alter Inhaber darf keine Werte oder Fortschritte nachschreiben. Ein
abgelaufener Cursor startet seine Partition begrenzt in einer neuen Receiptgeneration.
UPDATE friert die Überlappung zur letzten abgeschlossenen gespeicherten Bar ein.

Pro Seite gibt es maximal drei dauerhafte Wiederholungen mit1/2/4Sekunden Mindestabstand.
429 übernimmt einen auf60Sekunden begrenzten Backoff. Auch429 bestehender Datenleser
unterdrückt Researchrequests desselben Hosts. Nach1000Seiten oder15Minuten pro Lauf
wird der Job `PARTIAL`; Fortsetzung ist eine ausdrückliche Operatoraktion mit eigenem
Idempotenzschlüssel. Cursor und bereits bestätigte Seiten bleiben erhalten.

Alle Daten-GETs derselben Execution teilen einen Account-/Hostlimiter: höchstens
das bestätigte Entitlement und120GETs/Minute insgesamt. Research erhält höchstens
60/Minute,1/Sekunde und lässt60Slots/Minute für bestehende Leser reserviert. Bei
Entitlement≤60, unbekanntem Limit oder aktiver Safetyarbeit wird kein Researchslot
zugelassen. Order-POST/DELETE werden nicht als Researchrequests behandelt.

Research besitzt eigene SQLpools in Execution und privater API: jeweils1Verbindung,
kein Overflow,1Sekunde Poolwait,2Sekunden Statementtimeout,1Sekunde Locktimeout.
Der Adapter besitzt1HTTPverbindung,5Sekunden Gesamtdeadline und2MiB Antwortlimit.
Die API begrenzt Zeiträume auf366Tage und Seiten auf200Ergebnisse. Researchthreads
und APIaufnahme sind von den Sicherheitstakten getrennt.

## Audit, Prüfung und Wiederanlauf

`research.job_audit` protokolliert Jobstatus, Trace, Fortschritt, Retry und Fence in
derselben Transaktion wie die Zustandsänderung. Keine privaten Cursor, Provider-
Fehlermeldungen oder Payloads gelangen in diesen Audit. Runtimebenutzer können ihn
nicht verändern. Aggregierte Metriken enthalten nur feste Codes und Zahlen.
Researchschema und ACLs sind additive Migrationen0021–0027. Die vorhandenen
Handelsledger und deren Rechte bleiben unverändert. Downgrade löscht keine Daten.

Der echte Offlineintegrationstest verwendet einen lokalen HTTPproviderdouble,
den tatsächlichen Adapter und Coordinator, PostgreSQL mit echten Runtime-Rollen
und authentifizierte ASGI-Routen:

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_research_storage.py -q
```

Er erzeugt ausschließlich eine UUID-benannte Testdatenbank und Testrolle und entfernt
beide anschließend. Er greift weder auf persönliche Schlüssel noch echte Provider zu.
Bei `BLOCKED` zuerst den öffentlichen Fehlercode und die nicht geheime Quellenpolicy
prüfen. Keine privaten Seitentoken oder Credentialdateien zur Diagnose ausgeben.
Fehlende Evidenz wird durch Wiederaufnahme neu beschafft; alte Revisionen werden
nicht überschrieben. Eine Wiederaufnahme erteilt keine Handelsberechtigung.
