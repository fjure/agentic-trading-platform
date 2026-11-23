# Private modellgebundene Research-Evaluation

Der Betreiber führt die Evaluation erst nach verifizierter Registrierung, Custodian-Freigabe und gesonderter Modell- und Budgetautorisierung aus. Das Release-Gate bleibt bis zu vollständigen drei Läufen und den erforderlichen unabhängigen fachlichen Entscheidungen gesperrt.

## Vorbereitung

1. Die private Ablage unter der Betriebssystemidentität des Research-Supervisors bereitstellen. Verzeichnisse benötigen Modus 0700, private Dateien 0600. Die persistente Ablage liegt außerhalb des Repositorys. In der aktuellen Containerkomposition läuft der Supervisor unter UID/GID 10001. Die Ablage enthält das OperatorStore-Journal, `approval.json`, separat geprüfte `reviewer.pub` und `registrar.pub` sowie `deployment.json`.
2. Das vom Custodian freigegebene Eingabeverzeichnis ausschließlich lesend einhängen. Es enthält das verifizierte öffentliche Splitmanifest, die Entwicklungsreferenz, den freigegebenen Holdout, Reviewattestation und Signatur, Registrierungsbeleg und Signatur sowie die exakt registrierten Dateien `prompt.txt`, `schema.json` und `settings.json`.
3. Ein eigenes Evaluierungsmodellprofil bereitstellen. Sein Prompt verarbeitet `EVALUATION_CASE_V1` mit den Feldern `input` und `dialogueState`. Die vollständigen bestätigten Bedingungen sind im versionierten Fall enthalten. Das separate Ausgabeschema bindet die zu prüfende strukturierte Modellantwort. Profil, Modell, API, Prompt, Schema, Datensatz, Scorer, Einstellungen und drei Wiederholungen müssen exakt zur Betreiberfreigabe passen.
4. `settings.json` enthält ausschließlich `projectionEncoding` mit dem Wert `EVALUATION_CASE_V1` und `providerInstallationSha256` mit dem SHA-256-Wert des installierten Providerdeskriptors. `deployment.json` enthält `{"supervisorModule":"services.runtime.research_evaluation"}`. Der Deskriptor und die bestehenden Secrets werden über die bereits geprüften privaten Research-Mounts bereitgestellt.

## Ausführung

Die bisherigen Research-Overlays einschließlich Authority und Dispatch um `infra/compose/compose.research-evaluation.yaml` ergänzen. `ATL_EVALUATION_PRIVATE_DIR` und `ATL_EVALUATION_INPUT_DIR` auf die beiden privaten Hostverzeichnisse setzen. Die Komposition ersetzt für diesen Zeitraum den gewöhnlichen Supervisor. Sein Hintergrundzeitplan läuft währenddessen nicht. Setup-Widerruf bleibt über den signierten Authority-Rückkanal verfügbar.

Das Overlay startet standardmäßig mit `status`. Mit `ATL_EVALUATION_COMMAND=init` wird das neue private Journal provisioniert. Mit `register` werden Bindung, Custody und Registrierung vor dem Lesen des Holdouts validiert und die zulässigen Fälle dauerhaft registriert. Der gesondert autorisierte Befehl `run` führt die drei begrenzten Wiederholungen aus. Die bisherigen Compose-Projekt-, Profil- und Overlayparameter müssen bei jedem Befehl mitgeführt werden.

Ein Verbrauch des Journals oder ein bereits reservierter Versuch verhindert einen weiteren Versand desselben Attempts. Bei unbekanntem Ausgang bleibt die konservative Kostenreserve bestehen. Ein Prozessabbruch gibt keine verbrauchte Freigabe zurück. Rohantworten bleiben im privaten OperatorStore mit sieben Tagen Aufbewahrungsfrist. Der Befehl `purge` entfernt abgelaufene Rohantworten. Das gemeinsame PostgreSQL-Ledger erhält ausschließlich opaque IDs, Bindungen, Prüfsummen, Zustände und Abrechnung. Die Projektion verbleibt während des autorisierten Versands im flüchtigen Vault.

## Fachliche Entscheidung

Die redigierten Versandzustände belegen technische Ausführung. Die tatsächlich gespeicherten Modellantworten benötigen die fallbezogenen semantischen Annotationen, unabhängigen Erklärungsbewertungen und beobachteten Sicherheitsaktionen für den Scorer. Fehlende Beobachtungen sperren den modellgebundenen Bericht. Eine unabhängige Prüfung darf fehlende Beobachtungen weder durch Standardwerte noch durch Selbstauskünfte des Zielmodells ersetzen.

`python -m tests.evaluation --responses <privates-annotationspaket> --output <privater-bericht> --split holdout --holdout <privater-holdout> --manifest <registrierter-manifest> --development <registrierte-entwicklungsreferenz> --mode model --require-pass` führt die bestehende Scorer-Auswertung aus. Der Releaseprüfer `tests.evaluation.release.release_violations` verlangt vollständige autorisierte Kandidaten- und Baselinemengen und für Verschlechterungen eine durch vertrauenswürdige Review-Provenienz verifizierte Entscheidung für dasselbe Laufpaar. Ein leeres Verletzungsergebnis und die separate Betreiberentscheidung sind vor der späteren Profilfreigabe erforderlich. Drei erfolgreiche HTTP-Antworten allein erfüllen diese fachliche Abnahme nicht.

Nach der Auswertung den Supervisor mit der geprüften Standardkomposition wieder starten, den Evaluierungs-Mount entfernen und die Provider- sowie Zeitplanbereitschaft anhand der persistierten Zustände erneut prüfen.
