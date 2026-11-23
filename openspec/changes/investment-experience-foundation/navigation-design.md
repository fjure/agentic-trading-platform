# Navigation Design

## Ist und Ziel

Heute gibt es keinen Angular Router: `apps/dashboard/src/main.ts` registriert HTTP, `app.html` mountet den Supervisor, `supervisor.component.ts:98–108` hält Ansichtssignale und technische Navigation. `select()`/`selectPaper()` ändern per `history.replaceState` URL/Hash; `#alpaca-paper` wählt Paper. Companion nutzt `researchIdea`, `researchApproval`, `researchReport`, `researchReportVersion`; Quant nutzt `quantExperiment`, `quantBatch` und Reportparameter. Das sind reale Kompatibilitätsanforderungen, keine bereits vorhandenen fachlichen Seitenrouten.

IEF01 plant die neue Shell-/Routerbasis und Contracts; IEF08 baut darauf den persönlichen Einstieg. Einführung und Tests des Angular Router sind ausdrücklich Implementierungsarbeit nach separatem Apply-Auftrag. Der vorliegende Propose ändert keine Route und keinen Anwendungscode.

## Geplante Informationsarchitektur

| Hauptnavigation | Screens | Vorgeschlagener UI-Pfad | Hauptfrage |
| --- | --- | --- | --- |
| Mein Überblick | S2 Personal Dashboard | `/home` | Was ist jetzt relevant? |
| Portfolio | S3 Portfolio Hub, S4 Portfolio Insights | `/portfolio`, `/portfolio/insights` | Was ist bestätigt, was bedeutet es? |
| Strategien | S5 Strategy Discovery, S6 Strategy Detail | `/strategies`, `/strategies/:strategyId` | Was kann ich untersuchen? |
| AI-Aufgaben | S7 AI Task Center, S8 AI Task Detail | `/tasks`, `/tasks/:taskId` | Was untersucht das System für mich? |
| Aktivität | S10 Activity Timeline | `/activity` | Was ist geschehen? |
| Daten und Betrieb | ergänzende bestehende Ansichten | `/operations/...` | Wo finde ich Quellen, Gates und Diagnose? |

Guided Onboarding S1 unter `/onboarding` und Guided Paper Order S9 unter `/paper/orders/new` beziehungsweise lesender bestehender Vorgangsroute sind kontextuelle Wege, keine dauerhaften zusätzlichen Hauptmenüpunkte. Diese UI-Pfade sind Vorschläge; sie sind von geplanten `/api/v1/experience/*`-HTTP-Verträgen zu unterscheiden. Strategiedetail trägt Strategieversion als validierten optionalen Queryparameter; Taskdetail bindet seine Version serverseitig. Keine Geheimnisse oder freien Dialogtexte in URLs.

## Shell, Orientierung und Sicherheit

Desktop: sichtbare seitliche Hauptnavigation, klarer Seitentitel, Kontextmarkierung, Datenfrische und separate globale Zugänge zu tatsächlichen Paperkontrollen. Mobile: dieselben Ziele in beschriftetem Menü; wesentliche Kontextinformation bleibt außerhalb des geschlossenen Menüs. Browser-Zurück/Vorwärts und Reload funktionieren wie Seitenwechsel, nicht als Kommandoreplay. Deep Links bleiben nach Authentifizierung erhalten, ohne automatische Mutation.

Materialkonzept: seitlicher Modus `side` auf breiten Ansichten, `over` mit Backdrop auf kleinen Ansichten. Der überlagernde Bereich erhält Fokus und lässt sich per Escape/Backdrop schließen; Fokus kehrt zum Auslöser zurück. Navigationsziele sind echte Links mit zugänglicher Beschriftung und aktuellem Seitenstatus; Aktionsbuttons bleiben Aktionen. Keine interaktiven Elemente in Navigationslinks verschachteln. Diese Planung basiert auf den per Context7 abgefragten aktuellen offiziellen [Sidenav-Dokumenten](https://github.com/angular/material2-docs-content/blob/main/docs-content/overviews/material/sidenav/sidenav.md.html) und [Listen-Dokumenten](https://github.com/angular/material2-docs-content/blob/main/docs-content/overviews/material/list/list.md.html), Bibliothek `/angular/material2-docs-content`, Abruf 2026-11-23. Sie ersetzt keine versionspassende Verifikation beim Apply.

Breadcrumbs erscheinen bei Detailwegen: AI-Aufgaben → Auftragsname, Strategien → Strategiename, Portfolio → Insights. Anzeigenamen werden geladen; eine ID ist nur vorläufige technische Identifikation, kein fachlicher Titel. Bei fehlendem oder fremdem Objekt sichere Nichtverfügbarkeit mit Rücklink. Seitentitel und Fokus wechseln gemeinsam, Filterwechsel fokussiert nicht die ganze Seite neu. Ladeanzeigen blockieren nicht die komplette Shell.

## Zustände und Wiederaufnahme

URL trägt nur validierte Auswahl, paginierte Filter und Objektverweise; flüchtige Formulare/Sendefassungen bleiben aus URL, Browserpersistenz und Navigationstelemetrie. Präferenzen werden serverseitig actor+version gespeichert. Ein ungespeicherter fachlicher Entwurf bietet explizit Speichern oder Verwerfen beim Wegwechsel; keine Warnung bei bloßen Filtern. Bereits angenommene Vorgänge leben unabhängig von der Route weiter und sind nach Rückkehr serverseitig lesbar.

Abgelaufene Sitzung führt in den lokalen Aktivierungsweg und leert sensible flüchtige Felder. Nach Anmeldung geht es in eine lesende Detailansicht. Kein Resume, Taskstart, Modellversand oder Paperstart durch Redirect. Unbekannte Route bietet verständliche Rückkehr; ungültige IDs triggern kein Fallback auf ein anderes Konto oder einen anderen Scope.

## Kompatibilitätsmigration

1. Vorhandene Query-/Hashkombinationen inventarisieren und durch Tests belegen: `#alpaca-paper`, `researchIdea`, `researchApproval`, `researchReport` mit Version sowie `quantExperiment`, `quantBatch` und tatsächlich im Code unterstützte Reportparameter.
2. Legacy-Einstieg auf passende neue lesende Seite beziehungsweise ergänzendes bestehendes Detail abbilden; ursprüngliche Objektbindung bewahren. Widersprüchliche Auswahl wird erklärt, nicht durch beliebige Priorität still ersetzt.
3. Neue interne Links kanonische Routerziele verwenden lassen. Legacy Links bleiben ohne neue Mutation funktionsfähig. Reload auf verschachteltem Pfad benötigt bewusst konfigurierte SPA-Auslieferung, ohne APIpfade oder Authentifizierung umzuleiten.
4. Betrieb/Research/Quant entflechten, vorhandene Funktionalität und Datenfrischehinweise erhalten. Paper bleibt eigener Ledger. Fake/Demo unter ergänzender Navigation separat kennzeichnen.

Abnahme deckt sämtliche zehn Screens, Deep Link vor/nach Login, Browserhistorie, Reload, inkompatible Parameter, 401/404, schmale Ansicht, Tastaturfokus und pausier-/killbare bestehende Paperkontrollen unter Researchlast ab. Keine Routerguardprüfung ersetzt serverseitige Rechte oder Tradinggates.

## Übergabestand IEF-01

IEF-01 prototypisiert die vollständige Karte oben vor dem Start der abhängigen Featurepakete. Für S5/S6/S9/S10 werden belegte Eignung, Sperre, Rückweg und UNKNOWN gemeinsam dargestellt. Die Fokus- und Zustandskarte ist Teil der freigegebenen Prototypversion gemäß [UX-Validierung](ux-validation.md). IEF-08 übernimmt sie und prüft die echte Integration.
