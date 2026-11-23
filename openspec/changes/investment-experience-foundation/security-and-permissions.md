# Sicherheits- und Berechtigungsplan

Planungsstand, keine Implementierungs- oder Rechtsfreigabe. Es gelten `AGENTS.md`, `openspec/specs/risk-execution/spec.md`, die abgeschlossenen APF-Deltas und die ARP-Deltas. IEF vereinfacht die Bedienung bestehender Grenzen; es erteilt keine neuen Provider-, Daten- oder Handelsrechte.

## Lokale Identität und sechs getrennte Freigaben

V1 hat genau einen lokalen Betreiber. Einstieg und „Login“ verwenden die lokale Launcheraktivierung aus `services/control_api/auth.py`; kein Cloudkonto, Passwortregister, Rollenadministration oder Mehrbenutzerbetrieb. Die serverseitig ermittelte opake Actorreferenz wird zusammen mit Objekt- und Policyversion an Mutationen gebunden. Ein späterer Tenancy-Ausbau braucht einen eigenen Change mit Isolation, Migration und Autorisierungstests. Weder Browser noch Modell dürfen Actor, Eigentümer oder Berechtigungsumfang bestimmen. Ein strukturierter Nutzerkontext bleibt lokal und ist keine automatisch versendbare Promptbeilage.

| Freigabe | Erlaubter Effekt | Bindung und bewusste Entscheidung | Erteilt ausdrücklich keine |
| --- | --- | --- | --- |
| Auftragserstellung | Lokalen Analyseauftrag/Entwurf persistieren | Aktuelle Session, Auftrag, Version und idempotente Aktion; Nutzer bestätigt Ziel und Grenzen | Externe Verarbeitung, Kosten, Hypothesenbestätigung, Quantstart oder Paper |
| Datenversand | Genau die freigegebene Projektion an ein bereits zulässiges Modellprofil senden | Zweck, Inhalt, Datenklassen, Provider/Modell, Profil-/Credential-/Policyversion, Ablauf und Einmalverbrauch; vorhandene Processing-Preview wiederverwenden | Kostenbudget, fachliche Annahme, Quant oder Paper |
| Kosten | Begrenzte zulässige Modell-/Researchressourcen reservieren | Serverpreisstand, Währung, Obergrenze, Lauf/Versuch und Frist; Reservierung und Settlement atomar | Datenrechte, automatische Wiederholung bei unbekannten Kosten oder Tradingbudget |
| Hypothesenbestätigung | Konkrete fachliche Annahmen und ausdrücklich akzeptierte Approximation versionieren | Aktueller Dialog/Turn, Hypothese, Konflikte und geänderte Felder | Datenversand, Experimentstart, neue Registrystrategie oder Paper |
| Quantstart | Einen konkret geprüften Experimententwurf starten | Persistierte Definition, Snapshot, Researchmodus, Strategie-/Parameter-/Kosten-/Policybindung, frische Operatorprüfung und einmalige Admission | Automatische Folgeläufe, Modellkosten, Promotion, Paper oder LIVE |
| Paper | Eine konkrete manuelle Paperaktion beziehungsweise den separat begrenzten Paperbetrieb autorisieren | Bestehende Konto-/Capability-/Policybindung, gültiges Proposal und RiskDecision, aktueller Modus, Pause, Evidenz und finale Bestätigung | Riskumgehung, LIVE, Brokerzugriff für Agenten oder pauschale Freigabe aus einem Report |

Die sechs Entscheidungen dürfen als verständliche Schritte erscheinen, müssen serverseitig eigenständig prüfbar bleiben. Eine gemeinsame Schaltfläche „alles erlauben“ ist ausgeschlossen. Providerkonfiguration, Connectivity-Probe, Demo, Onboarding und ein positiver Backtest ersetzen keine dieser Entscheidungen. Kostenfreigabe kann bestehende begrenzte Budgetpolicy referenzieren, darf sie aber nicht stillschweigend erhöhen. Datenversand benötigt zusätzlich tatsächlich belegte Verarbeitungs- und Nutzungsgrundlagen; ein Consent ersetzt diese nicht.

Widerruf, Ablauf, Versionswechsel und Sessionverlust sperren neue Wirkungen. Bereits atomar angenommene Aktionen bleiben über ihre unveränderliche Identität abrufbar. Nach möglichem Versand werden Daten oder Kosten als möglicherweise verarbeitet ausgewiesen; kein erfundener Rückabwicklungsstatus. Reload, Wiederaufnahme und doppelte Klicks erzeugen keine neue Wirkung. Ein neuer inhaltlicher Versuch benötigt die jeweils neue gültige Bindung. Restore reaktiviert keine alten Berechtigungen oder Vorschauen.

## Daten, Darstellung und Prozessgrenzen

Portfolioanalyse verwendet zuerst den vorhandenen lokalen, autorisierten Kontosnapshot. Persönliches Vermögen, Kontodetails und Portfoliopositionen bleiben vom Zweck `HYPOTHESIS_INTERPRETATION_V1` ausgeschlossen. Eine optionale KI-Erklärung in J2 bleibt gesperrt, bis ein eigener eng begrenzter Erklärungszweck, minimierte Projektion, Klassifikation, nachgewiesene Grundlage und eigene Vorschau spezifiziert und implementiert sind. Lokale deterministische Erklärung bleibt immer verfügbar. Keine echten Portfoliodaten in Fixtures oder Browserartefakten.

Secrets erscheinen weder im Nutzerkontext noch in Reports, URLs, Browserpersistenz oder Telemetrie. Vorhandene write-only Setupgrenzen bleiben getrennt. Synthetische Canary-Werte prüfen Leakage; Agenten lesen keine echten Schlüssel. Modell- und Fremdtext wird ausschließlich passiv gerendert; keine aktiven Links, HTML, dynamischen Tools oder aus Text abgeleiteten Berechtigungen. Quantzahlen und Reportclaims werden aus autorisierten gespeicherten Referenzen gelesen, nicht vom Browser oder Modell berechnet. Demo und echte lokale Daten tragen sichtbare Herkunft und werden nicht zusammengeführt.

## Safety-Abnahme und Sperren

Die folgenden IEF-Prüfungen sind geplant und derzeit **unverifiziert**. Dokumentreview zertifiziert keine Laufzeit.

| Invariante / Bestand | Verpflichtender IEF-Nachweis | Blockierende Abweichung |
| --- | --- | --- |
| Agenten ohne Submit; nur Execution mit Brokeridentität, REQ-RSK-008 | J1–J4/J6: null Submit; J5: ausschließlich lokale Execution-Harnesswirkung; bestehende `tests/security/test_execution_isolation.py` | Research-/UI-/Gatewaypfad erreicht Broker oder Orderledger |
| Deterministische RiskDecision und aktuelle Execution-Gates, REQ-RSK-004/005 | J5 prüft unveränderliche Proposal-/Riskreferenz und Ablehnung bei zwischenzeitlichem Ablauf/Policywechsel | Frühere Vorschau umgeht aktuelle Prüfung |
| LIVE unerreichbar, DISABLED und unsicherer Neustart pausiert, REQ-RSK-001/002 | Modusmanipulation und Neustart zwischen Bestätigung und Wirkung; J6 zeigt sicheren Status | UI oder API öffnet LIVE beziehungsweise automatische Fortsetzung |
| Decimal, UTC und atomare Reservierung, REQ-RSK-006/007 | Dezimalgrenzen und konkurrierende Bestätigungen; getrennte Research-/Riskbudgets | Rundungsfehler oder doppelte Budgetverwendung |
| Unabhängige Pause/Kill und Reconciliation | Bestehende Safety-/Lastsuiten zusätzlich mit IEF-Navigation, Providerfehler und laufendem Research; keine UI-Abhängigkeit | Provider-/Jevfehler sperrt Pause oder Reconciliation |
| Unknown hält Reserve; kein blinder Retry, REQ-RSK-009/010 | Crash nach lokaler Submitannahme, Partial Fill, Cancel/Fill-Rennen, Reload und Statusabgleich | Zweiter Submit, verlorene Reserve oder falscher Fill |
| Credentials und Audit getrennt | Synthetische Canarychecks auf HTTP/DOM/Storage/Logs; Actor, Objektversion, UTC, Entscheidung und Trace korrelierbar | Secret/Rohprompt im Audit, fehlende Wirkungsspur |

## Regulatorische Planungsgrenze

Die geplante Ziel- und Risikokontextaufnahme ist keine zertifizierte Geeignetheitsprüfung. § 64 WpHG und Art. 25 MiFID II behandeln unter anderem besondere Beratungs-/Verwaltungspflichten und Geeignetheit; daraus folgt hier als Planungsentscheidung: keine Behauptung persönlicher Anlageberatung, regulatorischer Eignung oder einer Freigabe für Kundenbetrieb. Eine spätere Bereitstellung für Dritte benötigt eigenständige fachliche und rechtliche Prüfung. Quellen: [§ 64 WpHG](https://www.gesetze-im-internet.de/wphg/__64.html), [MiFID II Art. 25](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX%3A02014L0065-20260606).

Zweckbindung, Minimierung und Datenschutz durch Gestaltung/Voreinstellung motivieren die lokale Speicherung und getrennte Projektionsfreigabe; dies ist eine Gestaltungsableitung, keine Feststellung vollständiger DSGVO-Konformität. Datenfelder erhalten Zweck und Aufbewahrungsregel; ohne belegten Bedarf werden sie nicht erhoben. Keine Pflichtabfrage von Einkommen, Identitätsdokumenten oder persönlichem Gesamtvermögen für die lokale Demo. Quelle: [DSGVO Art. 5 und 25](https://eur-lex.europa.eu/eli/reg/2016/679). Offizielle Quellen am 23.11.2026 recherchiert; keine Rechtsfreigabe erteilt.
