# ADR 0003: Angular Material und begrenzter SignalStore

Status: geplant, 2026-10-12; Version/Primärquellen in `docs/development/technology-baseline.md`.

Angular Material erfüllt Tabellen, Formulare, Dialoge, Navigation, Status und responsive Layouts als kostenfreie Open-Source-Komponenten unter MIT. PrimeNG ist nicht zulässig. Native Einzelkomponenten wären mehr Accessibility-/Interaktionsarbeit; zusätzliche UI-Library wird vermieden.

NgRx SignalStore übernimmt ausschließlich geteilten Snapshot, Modus, Portfolio, Verbindungszustand, Cursor und Filter. Alternative reine Komponentenservices würde Reconnect-/Snapshotzustand mehrfach verwalten; klassischer globaler NgRx Store wäre für diesen kleinen Scope unnötig umfangreich. Formular-/Dialogzustand bleibt lokal. Backend bleibt Source of Truth, der Store enthält weder Risiko-/Brokerlogik noch Secrets und persistiert keine DTOs im Browser. Events/Selektoren und Resetregeln stehen in D-08. Ein Store wird erst nach verifizierter Versionskompatibilität installiert.
