# IEF-01 · Klickbarer Experience-Prototyp v1

Stand 2026-11-23. Technischer Interaktionsentwurf, synthetische Fixtures. **Nicht menschlich freigegeben.** Die fünf fachfremden Testpersonen stehen nach Nutzerangabe nicht zur Verfügung. Keine formative Erfolgsquote, kein Sicherheitsverständnis und keine Produktfreigabe werden aus automatisierten Tests abgeleitet. IEF-02–07 dürfen diesen Entwurf vor dokumentierter formativer Freigabe nicht als freigegebenen Flow übernehmen; integrierte Abnahme bleibt IEF-09.

## Umfang und Grenzen

Angular Router exakt22.2.2 ergänzt; Angular/Material bleiben22.2.2. Versionen und Integrität sind in apps/dashboard/package.json und package-lock.json gebunden. Context7 am23.11.2026: `/angular/angular/__branch__22.0.x` für Routermigration, `/angular/material2-docs-content/22.2.x-01b48b1` für optionalen Stepper und Sidenav-Fokus. Die Katalogversion ist kein Ersatz für die installierte exakte Paketversion.

`/` bleibt bestehender Supervisor. Gültige alte Root-Query-/Paperhashlinks werden lesend nach `/operations` mit sämtlichen ursprünglichen Objekt- und Versionsparametern adaptiert. Companion-Links dürfen Researchprovenienz mit Quantziel und Paperhash kombinieren; nichts wird entfernt. Mehrfach belegte IDs, ungültige Versionen, fehlende Berichtbindung und tatsächlich widersprüchliche Research-/Backtestauswahlen erzeugen eine verständliche sichere Rückkehr. Eine Quantreport-ID braucht ihre ursprüngliche Batch; Researchreport braucht ursprüngliche Version. Die alten Fachkomponenten und deren echte Kommandos bleiben unter Daten und Betrieb getrennt vorhanden. Paperkontrollen sind global verlinkt und führen zu vorhandenen echten Kontrollen; kein Klick führt deren Aktion aus.

Die Experience-Routen importieren keine APIclients, erzeugen keine GETs, POSTs oder Provider-/Brokerwirkung und speichern keine Daten. Die festen IDs `prototype-demo-01`, `snapshot-v1`, `analysis-prototype-01`, `prototype-operation-01` sind erkennbare Fixtureidentitäten. Reload stellt die fest ausgewählte Beispielansicht wieder her; das ist **kein** Nachweis von Analyse-/Präferenz-/Orderpersistenz. Freitext und editierbare Formfelder bleiben flüchtig, ohne URL, Browserstorage oder Telemetrie. Der gesamte Prototyp ist ständig gekennzeichnet. Neue Experience-Endpunkte werden nicht durch Placebo-Serverrouten simuliert.

## Screen- und Fokuskarte

| Screen | Route | Hauptaktion und Rückweg |
| --- | --- | --- |
| S1 | /onboarding | Optionalen Materialschritt überspringen, Demo ausdrücklich wählen → S2; zurück ohne Aktivierung |
| S2 | /home | Lokale Analyse → S4; Bestand → S3, offene Aufgabe → S8, Katalog → S5, Verlauf → S10 |
| S3 | /portfolio | Portfolio untersuchen → S4; separater Paperkontext → S9; Positionbeleg aufklappen |
| S4 | /portfolio/insights | Konkrete Demoquelle bestätigen → lokaler Befund S8; zurück S3 |
| S5 | /strategies | Katalogfixture filtern und Trendregelv1 verstehen → S6; kein Treffer getrennt von Quellausfall |
| S6 | /strategies/trend-long-flat?version=1 | Entwurf S8; historische Evidenz → aktuelle Eignung → separater Paperplan S9; Sperre/Rückweg |
| S7 | /tasks | Lokalen Befund, Researchfrage oder historischen Entwurf öffnen → S8 |
| S8 | /tasks/local-analysis, /tasks/tech-recovery, /tasks/quant-example | Befund/Original/Blocker lesen; neue Ersatzhypothese ausdrücklich annehmen → S6; zurück S7 und S10 |
| S9 | /paper/orders/new | Plan → Vorschau → lesende Risk-Vorprüfung → ausdrückliche simulierte Bestätigung → Status → S10; UNKNOWN liest dieselbe Operation |
| S10 | /activity | Quellengetrennte Ereignisse filtern, bestehenden Vorgang öffnen; lokale/Researchquelle → S8; Audit → bestehender Betrieb |

S6-Subschritte verwenden `phase=evidence|eligibility`. S9 verwendet `phase=preview|risk|confirm|status` und einen festen synthetischen Status. Strategie-/Versionsprovenienz bleibt bei Vorschau/Risk/Bestätigung im Query erhalten. Direkte und strategiebasierte Einstiege sind benannt. Historische Evidenz hat ausdrücklichen synthetischen Zeitraum2025, negative Validierung sowie fehlende OOS-/Holdoutphasen. Daraus entsteht keine automatische Promotion.

Fokusreihenfolge: Skiplink → Shell-Menü/Navigation → Seitentitel → Hauptaktion → sekundäre Aktionen → ergänzende Details. Skiplink fokussiert den Inhalt ohne Paperhash zu überschreiben. Seitenwechsel fokussiert die H1; querybasierte Filter behalten Fokus. Mobile-Menü fängt Fokus, Escape schließt mit Fokusrückgabe; Navigation fokussiert nach dem Schließen den Seitentitel. Onboarding verwendet sprechende optionale Schritte und unter768px vertikale Darstellung. Live-Statusmeldungen stehlen keinen Fokus. Vorhandener Supervisor erhält eine fokussierbare H1; fragmentbasierte Routerwechsel öffnen den richtigen Paperbereich auch bei wiederverwendeter Komponente.

## Zustände und sichere Entscheidungen

Jeder Screen besitzt unter „Prototypzustände und Prüfhinweise“ beziehungsweise `view=loading|empty|offline|stale|conflict|unauthorized|unavailable|ready` die feste Zustandsfixture. Texte nennen Ursache/Ungewissheit, Wirkung und den sicheren nächsten Schritt. Offline/stale sperren Bestätigung; Sitzungsverlust leert flüchtige Entwürfe; fremde Screen-ID, fremde Version oder fremder Demokontext wird nicht auf ein anderes Objekt umgedeutet. Der Demobestand wird nie unter einem Paperlabel ausgegeben.

S6-Eignung, S9-Risk und S10-Recovery decken einzeln Providerdeaktivierung, Budgeterschöpfung, stale, fehlende Datenrechte, nicht unterstützte Hypothese, ungültigen Quantgrant, PAUSED/HALTED, Sitzungsverlust und UNKNOWN ab. UNKNOWN bietet Lesen derselben Operation statt Ersatzorder; ausstehende Stornierung bedeutet weder Fill noch frei gewordenes Budget. Reale unabhängige Pause-/Killkontrollen bleiben erreichbar.

J4 nennt die genaue Techfrage, lokale Klärungsfragen und Testbarkeitsblocker. Die preisbasierte Frage erfindet keine Earningsdatenpflicht. Ersatz-Trendregel verliert ausdrücklich Techuniversum/Ereignis-/Wochenhorizont und wird nur nach getrennter bewusster Annahme geöffnet. Änderungen oder Verwerfen des Originals entziehen lokale Bestätigungen. Optionaler externer Erklärungszweck bleibt wegen fehlender konkreter Provider-/Daten-/Kosten-/Fristbindung gesperrt.

S9 unterstützt bewusst genau eine feste Fixture SPY/BUY/1/LIMIT100.00USD. Andere Eingaben erhalten eine explizite Blockade und werden nie still durch Fixturewerte ersetzt. Keine clientseitige Riskberechnung. Exakte Werte bleiben Strings. BUY-Bruttonotionalobergrenze und SELL-Bruttonotionaluntergrenze sind sprachlich getrennt; SELL ist im leeren synthetischen Paperkonto gesperrt. Demo-SPY-Bestand gehört zu einem anderen Ledger.

## Material-Tokens und Responsive-Verhalten

Das vorhandene Azure-Blue-Materialtheme bleibt erhalten. Gemeinsame semantische CSS-Tokens in src/styles.css: Hintergrund `#f4f7fb`, Oberfläche `#fff`, Text `#182b43`, Sekundärtext `#495a70`, Primäraktion `#214f91`, Fokus `#8b3900`, Erfolg `#175d39`, Warnung `#734500`, Blockade `#8c2735`, Fehler `#9d2020`, Grenze `#a0adbd`. Status besitzt immer Text, nicht nur Farbe. Materialbuttonlabels erhalten dieselbe Sans-Serif-Schrift wie der Inhalt. Keine erfundene Kurve und keine dekorative Performancegrafik.

Inhalt maximal1280px, Rand16/24px; Kartenpadding16/24px, Abstand24px. Unter768px einspaltig, ab768px zwei und ab1200px drei Karten. Navigation ab1024px `side`, darunter `over`. Aktionen mindestens44px, Titel28/36px, Abschnitt20/28px, Fließtext16/24px, Metadaten14/20px. Fokusumrandung3px; reduzierte Bewegung berücksichtigt. Browserprüfungen auf320/360/768/1440px prüfen horizontale Überläufe. Das ist ein technischer Nachweis, keine Screenreader- oder WCAG-Zertifizierung; manuelle Screenreader-/200%-Zoom-/Kontrastabnahme bleibt gesondert zu erheben.

## Reproduktion und technische Evidenz

Siehe docs/runbooks/ief-prototype.md. Gepinnter Node22.23.3 liegt im gemeinsamen lokalen Toolcache; der Build verwendet diese Version. Keine globalen Änderungen. Netzwerkzugriff war nur für npm-Installation erforderlich; Sandbox-DNS blockierte den ersten Versuch, der autorisierte genaue Installationsschritt und lokale Build/Chromiumtests wurden mit genehmigter Eskalation ausgeführt.

```powershell
npm ci --prefix apps/dashboard --offline=false
npm run build --prefix apps/dashboard
node apps/dashboard/node_modules/@playwright/test/cli.js test tests/e2e/ief-prototype.spec.ts tests/e2e/ief-hosting.spec.ts tests/e2e/agent-companion.spec.ts --config playwright.config.ts
```

Der produktionsoptimierte Build besteht. Initiales Bundle ca487.48kB unter500kB-Warngrenze. Bestehende Supervisor-CSS-Datei hat weiterhin ihre bereits vorhandene4kB-Warnung (7.12kB), unter8kB-Fehlergrenze.

Browsernachweise: alle zehn Screens und Reload; J1/J2 ausdrückliche Demoquelle und lokale Bestätigung; J3/J5 verbundene Evidenz-/Eignungs-/Plan-/Vorschau-/Risk-/Bestätigungs-/Statuskette mit Sperre; J4 Original und getrennte Ersatzhypothese samt Editinvalidation; J6 neun Pflichtfälle und identische UNKNOWN-Operation; alle Screenzustände; Mobilfokus/Escape und vier Breiten. Die Experiencewege erwarten exakt null APIrequests. Die bestehenden Companiontests öffnen tatsächlich generierte Report-/Experimentlinks mit Researchprovenienz und Paperwechsel, laden sie neu und prüfen unveränderte Entscheidungen. Hosttests trennen SPA von API-/Assetfehlern und verbieten UI-Mutationen. Konkrete Endergebnisse werden nach Abschluss des letzten Laufs ergänzt.

## Offene Freigaben

- Formative Prüfung aller sechs Wege durch mindestens fünf fachfremde Personen, davon mindestens zwei Mobile und zwei Desktop.
- Menschliche Verständniskontrolle von Research, historischem Backtest, Paper und bestätigter Ausführung;100% sichere Entscheidungen in Sicherheitsfällen.
- Befunde/Korrekturen/Nachtest und getrennte UX-/Produktfreigabe genau dieser Prototypfassung.
- Fachliche Serverimplementierungen und integrierte Prüfung bleiben in den abhängigen Paketen; kein technischer Prototyptest schließt diese Arbeit ab.

**Aktueller Abnahmezeitpunkt:** Nach dem späteren Nutzerauftrag werden alle drei menschlichen Prüfungen in IEF-09 durchgeführt. Frühere Aussagen in diesem Nachweis über eine menschliche Freigabe als technisches IEF-01- oder Übergabegate sind historisch. Der Prüfstatus bleibt HUMAN_UX_VALIDATION_PENDING.
