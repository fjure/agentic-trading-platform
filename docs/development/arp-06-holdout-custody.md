# Unabhängige Verwahrung des Research-Holdouts

Der lokale ARP-06-Katalog wurde fachlich unabhängig geprüft. Die Holdout-Datei unter `.tooling/output` ist eine synthetische Abnahme-Fixture. Mehrere Rollen teilen hier dieselbe Windows-Vertrauensgrenze. Ein Ausschluss aus Git liefert deshalb keinen Nachweis, dass Personen und Prozesse mit Zugriff auf die Modellentwicklung den Holdout vor der Registrierung nicht einsehen konnten.

Für einen modellgebundenen Lauf verwahrt ein unabhängiger Reviewer einen frischen synthetischen Holdout unter einem getrennten Konto oder in einer privaten Ablage mit eigener Zugriffssteuerung. Die Entwicklungsseite erhält vor der Registrierung nur Splitmanifest und Prüfsummen. Der Reviewer kontrolliert Fallabdeckung, Splitgrenzen und die lokale Behandlung der Offline-Fälle. Die Referenzrevision wird nach einem Einblick mit anschließender Optimierung ausgetauscht. Bewertungen auf einem bereits gesehenen Holdout bleiben historische Vergleiche.

1. Der Betreiber hinterlegt den Ed25519-Public-Key des Reviewers als separat provisionierte 32-Byte-Datei. Die Herkunft des Pins wird unabhängig vom Holdout-Paket geprüft. Der private Schlüssel bleibt beim Reviewer.
2. Der Reviewer registriert die unveränderliche Bindung für Kandidat und gegebenenfalls Vergleichsstand, bevor er den Holdout freigibt. Sie umfasst Profil, Modell, API, Fall-, Prompt-, Schema-, Scorer- und Settingsprüfsummen sowie die drei Wiederholungen. Er bindet das Splitmanifest und den freizugebenden Holdout an diese Registrierung.
3. Der Reviewer produziert für jede registrierte Bindung das unten stehende JSON. Die UTC-Zeitwerte im ISO-8601-Format mit `Z` dokumentieren die von ihm geprüfte Registrierung und anschließende Öffnung. Er signiert `attestation_message(document)` mit seinem Ed25519-Schlüssel und legt die 64 rohen Signaturbytes separat ab. Holdout und Attestierung werden ausschließlich über den vereinbarten privaten Betreiberpfad bereitgestellt.
4. Der Operator verifiziert die Signatur gegen den unabhängig hinterlegten Pin und die exakten Bindungshashes. Der Betreiber benötigt zusätzlich eine eigene persistierte Freigabe. Der signierte Nachweis belegt die Erklärung des zuständigen Reviewers. Ihre betriebliche Grundlage ist dessen unabhängige Verwahrung.

Die Attestierung setzt `version` auf 1. `holdoutSha256` enthält die Prüfsumme der privaten Holdout-Datei und `manifestSha256` die des Splitmanifests. `bindingSha256` wird mit `run_binding_sha256(binding)` aus der registrierten `RunBinding` gebildet. Die beiden Funktionen stehen in `tests.evaluation.custody`. Das vollständige Beispiel enthält ausschließlich Platzhalter.

```json
{
  "version": 1,
  "holdoutSha256": "<SHA-256 der Holdout-Datei>",
  "manifestSha256": "<SHA-256 des Splitmanifests>",
  "bindingSha256": "<SHA-256 der Bewertungsbindung>",
  "registeredAt": "2026-01-01T09:00:00Z",
  "openedAt": "2026-01-01T10:00:00Z"
}
```

## KI-Review und Custodian ausführen

Der Betreiber isoliert zwei Reviewer-Kontexte und stellt je Kontext ausschließlich den eingefrorenen Fallentwurf, die kanonischen Quant-/Backtestingverträge und den versionierten Reviewprompt bereit. Er bestätigt die Berichtsherkunft unabhängig von den Selbstauskünften des Modells. Der Runbindungs- und Registrierungsbeleg muss aus einem getrennt verwalteten externen Register stammen. Die Tests verwenden dafür synthetische Registerquittungen.

`python -m tests.evaluation.custodian_cli init|seal|release --config <privat> --input <privat> --revision <Nr>` läuft unter der separaten Custodian-Identität. Der Befehlsname ist einzeln zu wählen. `release` schreibt in ein neues privates Verzeichnis über `--output`. Das Quellpaket für `seal` enthält die Holdout- und Splitdatei, die öffentliche Entwicklungsdatei und beide strukturierten Reviewberichte. Die Ausgabedateien enthalten den Holdout, das Splitmanifest und die signierten Nachweise. Der Ledger wird unabhängig vom verschlüsselten Store gesichert und bei einem Payload-Restore weiterverwendet. Ein unterbrochener Übergabevorgang gilt als verbraucht.

`custodian.json` enthält `developmentUid`, `reviewerUid`, `targetProvider`, `state` und `releaseLedger`. Schlüsseldateien sind `storage.key` und `custodian.key`, jeweils 32 Byte aus der separaten Betreiberprovisionierung. `registrar.pub` und `operator.pub` sind getrennte Ed25519-Pins. Zur Freigabe hinterlegt der Betreiber `binding.json`, `registration.json`, `registration.sig`, `release.json` und `release.sig`. Das Evaluationskonto hält zusätzlich `reviewer.pub` als Pin für den Custodian und `registrar.pub` als Pin für den Registrar. Bei Schlüsselverlust stoppt der Betreiber die Instanz, entfernt den widerrufenen Pin und provisioniert eine neue Schlüsselbindung. Der separat gesicherte Freigabeledger bleibt erhalten.
