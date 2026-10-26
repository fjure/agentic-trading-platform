# Architekturprinzipien

Dieses Dokument beschreibt Leitlinien für die Weiterentwicklung. Verbindliche Sicherheitsregeln stehen in [AGENTS.md](../../AGENTS.md), konkrete Anforderungen in OpenSpec und HTTP-Verträge in `contracts/http/control-api.openapi.yaml`.

## Grenzen vor Bequemlichkeit

Research, Strategie, Risk, Execution, Control und Überwachung erhalten getrennte Aufgaben und Berechtigungen. Research und Entscheidungsagenten erzeugen keine Brokeraktionen. Risk und Execution prüfen Eingaben deterministisch und geschlossen bei Fehlern. Menschliche Konfiguration oder eine Agentenantwort erteilt keine Handelsfreigabe. LIVE bleibt unerreichbar.

Daten sollen unveränderlich und versioniert erfasst werden. Eine spätere Korrektur erzeugt eine neue nachvollziehbare Sicht, die frühere Entscheidungen nicht umschreibt. Jede historische Auswertung muss auf den damaligen Verfügbarkeitszeitpunkt begrenzt werden können. Point-in-time (PIT) ist ein Ziel für künftige historische Forschung; die vorhandenen Zeitfelder und der Replaycache allein belegen noch keine historische PIT-Pipeline.

## Einheitliche Daten und exakte Rechnung

Preise, Mengen, Kontostände, Gebühren und Risklimits verwenden Decimal. Technische Zeitstempel werden als UTC gespeichert; Börsenkalender behalten zusätzlich ihre definierte Zeitzone und Version. Quellen, Instrumente, Einheiten, Anpassungen und Qualitätsstatus müssen erkennbar bleiben. Unbekannte oder fehlende Evidenz wird ausgewiesen und kann abhängige Entscheidungen sperren.

Jev und andere Modelle erhalten nur die für ihre Aufgabe nötigen, redigierten Daten. Modellantworten bleiben Vorschläge oder Shadow-Evidenz. Provider-Schlüssel bleiben außerhalb von Git, Logs und Modellkontext; Alpaca-Paper-Credentials erhält nur Execution, Jev-Credentials nur der Worker. Der zugelassene lokale Einrichtungsdialog überträgt Schlüssel flüchtig an den Setup-Dienst und startet keinen Handel.

## Verträge und überprüfbare Änderungen

OpenSpec hält das fachliche Verhalten fest. Der OpenAPI-Vertrag ist die Quelle für HTTP-Schnittstellen; generierte Clients werden reproduzierbar erzeugt. Persistenz, Rollenrechte, Fehlerpfade, Reconciliation und Audit gehören zur Abnahme einer Funktion. Neue Datenquellen brauchen überprüfbare Lizenz- und Nutzungsrechte, Qualitäts- und Korrekturregeln sowie dokumentierte Herkunft.

## Heute verwendete Technik

Der Backend-Code benötigt Python 3.13 und verwendet unter anderem FastAPI, Pydantic, SQLAlchemy, Alembic und PostgreSQL. Der Webclient nutzt Angular 22, Angular Material, TypeScript 6 und RxJS; PrimeNG ist ausgeschlossen. NgRx SignalStore ist vorhanden, aber nur für begründet geteilten UI-Zustand vorgesehen. Docker Compose betreibt lokale Dienste mit getrennten Runtime-Rollen.

Das ist der beobachtete Stack, keine Verpflichtung zu neuen Diensten oder Frameworks. Insbesondere ist LangGraph nicht adoptiert. Architekturentscheidungen zu Orchestrierung, Datenhaltung oder Providern werden erst mit einem konkreten OpenSpec-Change und nachvollziehbarer Abwägung getroffen. Siehe [Marktevidenz-Spezifikation](../../openspec/specs/market-evidence/spec.md) und [Jev-Evaluationsrunbook](../runbooks/jev-evaluation.md).
