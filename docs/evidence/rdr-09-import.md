# RDR-09 – lokaler Trusted-Evidence-Import

Stand: 27.10.2026. Dieser Nachweis beschreibt ausgeführte Softwareprüfungen mit
synthetischen Autoritäten. Er bestätigt keine reale Quelle, unabhängige reale
Publikation, Providerrechte oder historische Nutzbarkeit. Produktion beginnt
weiterhin mit einem leeren Quellenregister; keine Sammlung oder Handelsfreigabe
wird durch einen Import aktiviert.

## Implementierter Umfang

`packages/research/evidence_import.py` begrenzt die UTF-8-Eingabe vor dem Parsen
auf 1 MiB und anschließend auf das geschlossene RDR-01-Bündelschema. Doppelte
JSON-Schlüssel, ungültiges Unicode, nicht erlaubte Zahlen, freie Felder,
mitgelieferte Trustanker und behauptete Ingestion-/Commitwerte werden verworfen.
SHA-256 und abgetrennte Ed25519-Signaturen binden echte RFC-8785-Bytes; die
Anwendung besitzt keine Signierfunktion.

`packages/research/evidence_trust.py` prüft ausschließlich vorab registrierte
öffentliche Schlüssel, Quelle, Instrument, Belegtyp, Intervall, Signierzeit,
aktuelle Gültigkeit, Quellen-/Zeugenverfahren und aktuelle Widerrufsentscheidungen.
Schlüsselrotation verwendet eine neue Schlüsselidentität. Eine bestehende
Schlüsselidentität kann weder ihren öffentlichen Schlüssel noch ihren Algorithmus
wechseln. Korrektur und Rücknahme sind neue Entscheidungen mit Vorgängerbindung.
Unbekannte Kompromittierungszeit sperrt die betroffene Kette konservativ.

Ein unabhängiger Offlinezeuge muss den exakten Inhalts- und Aussagedigest sowie
Quelle, Evidenzidentität, Revision, Instrument und Intervall binden. Ein eigener
Quellenschlüssel kann nicht gleichzeitig der unabhängige Zeuge sein.
`EXISTENCE_BY` bleibt für historische Veröffentlichung UNKNOWN/TIME_UNPROVEN.
`PUBLICATION_OBSERVED_BY` verifiziert nur die zugelassene Aussage zur beobachteten
Verfügbarkeit. Die synthetische Abnahme beweist die Softwareprüfung dieses
Verfahrens, nicht die Unabhängigkeit eines realen Zeugen.

Integrität, Quellenauthentizität, zeitliche Authentizität, Vollständigkeit und
Domainqualität bleiben getrennt. Eine Quellenbehauptung zu Qualität liefert
keine fehlenden OHLC-Bars. Negative Finanzwerte, ungültige Kalenderintervalle und
unbelegte zukünftige Kapitalmaßnahmenfreiheit werden durch eine gültige Signatur
nicht zu positiven Domainbelegen. Research Eligibility wird vom gemeinsamen
Assessor ermittelt und ist kein Eingabefeld des Imports.

Der RDR-03-Port speichert Belege, ursprüngliche normalisierte Signaturprovenienz,
Receipt, Trustversion und gemeinsame Research-Commitversion atomar. Die
Ingestionzeit stammt ausschließlich aus der Datenbank. Gleicher Bündelhash
liefert denselben Receipt; ein bereits an andere Nutzdaten gebundener Schlüssel
erzeugt einen Konflikt. Aktuelle Truständerungen zwischen Prüfung und Aufnahme
blockieren die Aufnahme unter derselben Trustzustandssperre. Korrekturen erhalten
eigene unveränderliche Revisionen mit tatsächlichem Vorgänger.

Die Commitbestätigung erfolgt erst in einer neuen Transaktion. Ein Ausfall dieser
Bestätigung meldet `COMMIT_CONFIRMATION_UNKNOWN` und behauptet keinen Rollback
bereits committed Inhalte. Wiederholung kann den ursprünglichen Receipt erneut
bestätigen. Abgewiesene Importe werden nach Rollback in einem eigenen,
append-only Ablehnungsjournal ausschließlich mit redigiertem Code und DB-Zeit
erfasst; ungültige Nutzdaten gelangen nicht in den Belegbestand.

## Lokale Bedienung und Rechte

`scripts/import-research-evidence.py --help` wurde ausgeführt. Das Einmalwerkzeug
bietet `admit` für ausdrückliche lokale Betreiberentscheidungen und `import` für
normalisierte Bündel. Es verwendet einen lokalen libpq-Service unter
`atl_evidence_importer`, bindet sowohl `host` als auch das tatsächlich verbindende
libpq-Feld `hostaddr` an `127.0.0.1` und nimmt keine Passwörter
oder privaten Schlüssel als Argumente entgegen. Die Serviceauthentifizierung
richtet der lokale berechtigte Betreiber außerhalb von Git ein. Der CLI kann
den Produktionsmodus nicht abschalten; Testautoritäten werden dort abgewiesen.

Die Certifierrolle kann keine Orders, Auditledger oder Researchjobs schreiben
und keine vorhandene Evidenz ändern. Runtime-Control, Execution und Worker
können den Importport nicht verwenden. Gegenseitige Grenzen der Import- und
Beobachtungs-Commitbestätigung sind zusätzlich in der RDR-03-Rollenabnahme geprüft.
Es wurden keine echten Provider oder Handelsendpunkte aufgerufen.

## Abhängigkeiten und Sicherheitskorrekturen

Gepinnt wurden `cryptography==50.0.2` und `rfc8785==0.1.4`; Auflösung und lokale
Synchronisierung erfolgten mit dem installierten `uv 0.8.22` und explizitem
Projektinterpreter. Die Implementierung verwendet die dokumentierte
[Ed25519-Verifikation von PyCA](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)
und die [RFC-8785-Implementierung von Trail of Bits](https://github.com/trailofbits/rfc8785.py).
Context7 war wegen ausgeschöpfter Anbieterquote nicht verfügbar; die aktuellen
Primärdokumentationen und PyPI-Releases wurden direkt geprüft.

Der erste vollständige lokale `pip-audit` fand bereits zuvor gelockte Befunde:
`certifi==2026.7.22` mit PYSEC-2026-4320 und `filelock==4.1.1` mit
CVE-2026-112906, -112940, -112914, -112913, -112937 und -112907. Der explizit
freigegebene gezielte Refresh änderte ausschließlich diese vorhandenen transitiven
Pakete auf `certifi==2026.10.19` und `filelock==4.2.1`; die neuen direkten Pins
blieben gleich. Grundlage sind die
[certifi-Sicherheitsmeldung](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)
und die [filelock-4.2.1-Freigabe](https://github.com/tox-dev/filelock/releases/tag/4.2.1).
Der anschließende Audit meldete **No known vulnerabilities found**; das editierbare
eigene Projektpaket wird vom Werkzeug ausdrücklich übersprungen.

## Ausgeführte Prüfungen

```powershell
./.venv/Scripts/python.exe -m pytest tests/security/test_research_evidence_trust.py tests/integration/test_research_evidence_import.py -q
# 42 passed in 42.44s

./.venv/Scripts/python.exe -m ruff check packages/research/evidence_import.py packages/research/evidence_trust.py scripts/import-research-evidence.py tests/security/test_research_evidence_trust.py tests/integration/test_research_evidence_import.py
# All checks passed!

./.venv/Scripts/python.exe -m pip_audit --local --skip-editable --progress-spinner off --cache-dir .tooling/output/rdr09/audit-cache
# No known vulnerabilities found

uv lock --python .venv/Scripts/python.exe --cache-dir .tooling/output/rdr09/uv-cache --check
# Resolved 72 packages
```

Die echte PostgreSQL-Abnahme erzeugt eine isolierte temporäre Datenbank und
Loginidentität. Generierte Testzugangsdaten werden ausschließlich über stdin
übertragen und weder als Argument noch Datei oder Ausgabe persistiert. Ein
schreibgeschützter, unprivilegierter Testcontainer erhält nur die lokale
Docker-Datenbankverbindung; Datenbank und Login werden anschließend entfernt.
Das lokale Hilfsimage `agentic-trading-lab-rdr09-test:local` ergänzt das bestehende
Researchtestimage um die beiden gepinnten Kryptografiepakete; seine Dockerdatei
liegt ausschließlich unter dem ignorierten `.tooling/output/rdr09/`.

Nachgewiesen wurden echte Ed25519-Manipulationsfälle, leeres Produktionsregister,
Testschlüsselausschluss, Scope, Schlüsseltausch und Rotation, Widerruf mit
unbekannter Kompromittierungszeit, unabhängiger Publikationsbeweis und unzureichender
Existenzbeweis. PostgreSQL prüfte Gesamtrollback trotz zuvor eingefügtem Eintrag,
zwei parallele Importe mit einem Receipt, Schlüssel-/Nutzlastkonflikte,
Revisionsvorgänger, heutige Ingestion ohne frühere Sichtbarkeit, redigierte
Ablehnungsjournale und das Widerrufsrennen vor atomarer Aufnahme.

Die vorhandenen Coverage-Leseports wurden tatsächlich verwendet: gültige
Quellsignatur ohne Zeitbeweis ergab TIME_UNPROVEN, synthetisch unabhängig
publikationsgebundene Coverage wurde nach heutigem Import VERIFIED, eine spätere
negative Revision wurde NEGATIVE. Frühere Snapshots blieben unverändert. Ein
aktueller Widerruf sperrte neue Verwendung; gespeicherte ursprüngliche
Vertrauensdimensionen blieben bytegleich.

## Noch getrennt abzunehmende Grenzen

Dieser Nachweis ersetzt weder den unabhängigen Review noch Issue-Sync und
geprüfte Gitintegration. Die gemeinsame RDR-08-Abnahme muss zusätzlich Sammlung,
dauerhafte Diagnosejobs, echte HTTP-/Browserpfade und Safetylast zusammen prüfen.
Reale Quellenzulassung und Provideraktivierung bleiben ausdrücklich lokale
Betreiberhandlungen. Das Produktionsregister wurde durch diese Arbeit nicht
befüllt.
