# ADR 02 · Getrennte Abnahmen

Status: vorgeschlagen.

Offline-CI beweist Protokoll, Deployment, Recovery und Sicherheitsgrenzen. Ein einzeln autorisierter synthetischer Smoke-Test belegt Erreichbarkeit und Interoperabilität einer konkreten Bindung. Eine separate versionierte Evaluation bewertet Modellqualität. Deterministische Fixture-Antworten können Semantik nur als Auswertungsorakel überprüfen. Live-Ausführung als CI-Voraussetzung würde Kosten, Secrets und externe Verfügbarkeit in das Merge-Gate einführen.
