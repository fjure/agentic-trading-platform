# Traceability

Verbindliche Zuordnung aller Requirements und Szenarien zu Arbeitspaketen, Nutzerreisen und späterer Prüfevidenz. In Propose sind keine Implementierungstests als bestanden ausgewiesen.

| Requirement | Capability / normative Szenarien | Pakete | Journeys | Geplanter Nachweis |
| --- | --- | --- | --- | --- |
| IEF-G01 Gebundene verständliche Ordervorschau | [guided-paper-experience](specs/guided-paper-experience/spec.md); IEF-T-G01 | IEF-01, IEF-07 | J5 | Contract + API/SQL + Browser + Negativfälle |
| IEF-G02 Getrennte ausdrückliche Paperbestätigung | [guided-paper-experience](specs/guided-paper-experience/spec.md); IEF-T-G02, IEF-T-G03 | IEF-07 | J5,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-G03 Nachvollziehbare unsichere Ausgänge | [guided-paper-experience](specs/guided-paper-experience/spec.md); IEF-T-G04, IEF-T-G05 | IEF-07 | J5,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-C01 Kohärente Navigation und Dashboard | [investment-cockpit](specs/investment-cockpit/spec.md); IEF-T-C01, IEF-T-C02 | IEF-01, IEF-08 | J1-J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-C02 Ehrliche Zustände und zugängliche Bedienung | [investment-cockpit](specs/investment-cockpit/spec.md); IEF-T-C03 | IEF-08 | J1-J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-C03 Quellengebundene Activity Timeline | [investment-cockpit](specs/investment-cockpit/spec.md); IEF-T-C04 | IEF-08 | J1-J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-C04 Reproduzierbare integrierte Abnahme | [investment-cockpit](specs/investment-cockpit/spec.md); IEF-T-C05 | IEF-09 | J1-J6 | Produktionsbuild + echte temporäre PG/API/Runtime + J1-J6 + Pflichtmanifest/CI |
| IEF-O01 Überspringbare Einrichtung | [investment-onboarding](specs/investment-onboarding/spec.md); IEF-T-O01, IEF-T-O02 | IEF-02 | J1 | Contract + API/SQL + Browser + Negativfälle |
| IEF-O02 Versionierte Betreiberpräferenzen | [investment-onboarding](specs/investment-onboarding/spec.md); IEF-T-O03, IEF-T-O04 | IEF-02 | J1 | Contract + API/SQL + Browser + Negativfälle |
| IEF-A01 Persistenter versionierter Arbeitsauftrag | [investment-task-experience](specs/investment-task-experience/spec.md); IEF-T-A01 | IEF-01, IEF-04, IEF-06 | J1,J4 | Contract + API/SQL + Browser + Negativfälle |
| IEF-A02 Tatsächliche Fähigkeiten und klare Zustände | [investment-task-experience](specs/investment-task-experience/spec.md); IEF-T-A02, IEF-T-A03 | IEF-06 | J4,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-A03 Eigenständige Autorisierungsentscheidungen | [investment-task-experience](specs/investment-task-experience/spec.md); IEF-T-A04, IEF-T-A05 | IEF-06 | J2,J3,J4,J5 | Contract + API/SQL + Browser + Negativfälle |
| IEF-A04 Sichere Wiederaufnahme und Abbruch | [investment-task-experience](specs/investment-task-experience/spec.md); IEF-T-A06 | IEF-06 | J4,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-P01 Konsistente Portfolioansicht | [portfolio-experience](specs/portfolio-experience/spec.md); IEF-T-P01, IEF-T-P02 | IEF-01, IEF-03 | J1,J2 | Contract + API/SQL + Browser + Negativfälle |
| IEF-P02 Verfügbarkeit statt Ersatzwerte | [portfolio-experience](specs/portfolio-experience/spec.md); IEF-T-P03, IEF-T-P04 | IEF-03 | J2,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-P03 Deterministische nachvollziehbare Insights | [portfolio-experience](specs/portfolio-experience/spec.md); IEF-T-P05, IEF-T-P06 | IEF-04 | J1,J2 | Contract + API/SQL + Browser + Negativfälle |
| IEF-P04 Getrennte optionale KI-Erklärung | [portfolio-experience](specs/portfolio-experience/spec.md); IEF-T-P07, IEF-T-P08 | IEF-04 | J2,J6 | Contract + API/SQL + Browser + Negativfälle |
| IEF-S01 Verständliche versionierte Strategien | [strategy-discovery](specs/strategy-discovery/spec.md); IEF-T-S01, IEF-T-S02 | IEF-05 | J3,J4 | Contract + API/SQL + Browser + Negativfälle |
| IEF-S02 Evidenzgebundener Status und Vergleich | [strategy-discovery](specs/strategy-discovery/spec.md); IEF-T-S03, IEF-T-S04 | IEF-05 | J3 | Contract + API/SQL + Browser + Negativfälle |
| IEF-S03 Geführte Forschungsübergänge | [strategy-discovery](specs/strategy-discovery/spec.md); IEF-T-S05 | IEF-05 | J3 | Contract + API/SQL + Browser + Negativfälle |

## Feature- und Screenabdeckung

| Auftrag | Führendes Paket / Dokument |
| --- | --- |
| A Guided Onboarding | IEF-02; personas.md, ux-architecture.md Screen1 |
| B Personal Dashboard | IEF-08; navigation-design.md, Screen2 |
| C Portfolio Hub | IEF-03; portfolio-read-model.md, Screen3 |
| D Portfolio Intelligence | IEF-04; design.md D3, Screen4 |
| E Strategy Discovery/Detail | IEF-05; strategy-experience.md, Screens5/6 |
| F AI Task Center/Detail | IEF-06; agent-task-model.md, Screens7/8 |
| G Guided Paper Order | IEF-07; guided-order-design.md, Screen9 |
| H Activity Timeline | IEF-08; Screen10 |
| Optional Daily Briefing | bewusst zurückgestellt; konsumiert später dieselben Projektionen, keine Newsquelle in IEF |
| Zukunft Identität/Recht | security-and-permissions.md; kein Mehrbenutzer-/Anlageberatungsbau |

## Evidenzdateien im späteren Apply

Jedes Paket führt `docs/evidence/ief-NN.md` mit Quell-SHA, Werkzeug-/Datenversionen, Kommandos, Resultaten, Negativfällen und unabhängigen Befunden. IEF-09 ergänzt `docs/evidence/ief-integrated-acceptance.md` und ein maschinenlesbares J1–J6-Pflichtmanifest. Run-/Analyse-/Approval-/Order-/Traceidentitäten sind referenzierbar, Inhalte bleiben synthetisch und secretfrei. Keine Screenshotbehauptung ohne tatsächliche Browserausführung. Issue- und CI-Mappings sind getrennte Planungsnachweise und kein Ersatz für fachliche Laufzeitprüfungen.
