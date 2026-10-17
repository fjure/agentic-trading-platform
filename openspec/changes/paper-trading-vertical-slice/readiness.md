# Definition of Ready und Prüfevidenz

Aktualisierung2026-10-18: PTV-21 ist nach tatsächlichen Deployment-, Windows-Transport-, SQL-Ausfall-, Contract- und unabhängigen Safetyprüfungen abgeschlossen;23/25Tasks. Die folgende frühere22/25-Aktualisierung ist historisch. Der Fake-Default bleibt DISABLED/PAUSE_NEW und hat keine echten Anbietercredentials. PTV-22 (menschliches Opt-in-Smoke vorbereiten) und PTV-23 (abschließende Integrationsverifikation) bleiben offen; keine vollständige Paper-Betriebsfreigabe aus DoR ableiten. Evidenz: docs/evidence/ptv-21-runtime-boundaries.md.

Aktualisierung2026-10-17: Apply ist seit dem späteren Nutzerauftrag gesondert freigegeben. Die ausdrücklich bestätigte Erweiterung um PTV-24/25 wurde vor PTV-21 eingeordnet;25LeafTasks und25eindeutige Issues sind mit vollständigem Read-back synchronisiert. PTV-01..20 und PTV-24/25 sind abgeschlossen (22/25); PTV-21..23 bleiben offen. Die vollständige Paper-Prozess-/Deploymentfreigabe bleibt bis zu deren Evidenz geschlossen. Die nachstehende DoR-Tabelle dokumentiert den historischen Bootstrapstand und behauptet keine aktuelle Runtime-Zertifizierung.

Historischer Stand 2026-10-12, letzter GitHub-Read-back 16:50:15 UTC. **Definition of Ready für die Implementierungsplanung erfüllt.** Der damalige Auftrag endete nach Spezifikation und Synchronisierung; Anwendung benötigte einen eigenen ausdrücklichen Apply-Auftrag. Runtime-Zertifizierung folgte nach Umsetzung. Damals waren alle23 Anwendungstasks offen.

| Gate | Ergebnis und Evidenz |
|---|---|
| OpenSpec-Artefakte | PASS: Proposal, 5 Deltas, Design, Tasks; CLI 1.14.1 `validate paper-trading-vertical-slice --strict --json`, 1/1 valid, 0 issues |
| Requirements/Szenarien | PASS: 40 eindeutige Requirements mit testbaren Szenarien |
| Traceability | PASS: `python scripts/validate-plan.py --write`, 40 Requirements → Design → Contract/Schema → Task → geplanter Test; `traceability.json` und `traceability.md` |
| Taskabhängigkeiten | PASS: 23 eindeutige Leaf-Tasks; vollständiger topologisch geordneter Graph; begrenzte Parallelität/Dateizuständigkeit in tasks.md |
| Externe Ports | PASS im Entwurf: 7 Ports, private Vendoradapter, Secret- und Submitgrenzen D-01/D-02 |
| Safety/Geld | PASS im Entwurf: Decimal/UTC, fail-closed Modes/TTL/Policygate, atomare Reservierung, UNKNOWN/Reconcile, Cancel/Fill; alle einer Test-ID zugeordnet; Runtime unverified |
| LIVE / Agentenorders | Ausgeschlossen und als harte spätere Guards spezifiziert; keine Anwendung oder Order in diesem Durchlauf |
| Contracts/Beispiele/Generator | PASS: OAS 3.1.2, 18 Operationen, 8 Schemas, Beispiele und Negativfälle; Redocly ohne Warnungen; Generator 7.26.0 zweimal 65 bytegleiche Dateien; Angular 22.2.2/TS 6.0.2 inklusive echter Erfolgs-/ABSTAIN-Typfixtures grün. `docs/development/contract-validation.md` |
| Angular Material/Store | PASS im Entwurf: Material MIT/kostenfrei, Angular-/NgRx-22-Baseline, SignalStore nur geteilter Anzeigezustand, ADR 0003; keine PrimeNG-Abhängigkeit |
| Skills | PASS: 11/11 statisch valide und im Sitzungskatalog erkannt |
| MCPs | PASS: ausschließlich bestehende Projektserver Context7 4.3.0, Playwright MCP 0.0.83, official Hosted GitHub; effektive Inventur/Allowlisten geprüft; keine globale Änderung |
| GitHub Auth/Ziel | PASS: `get_me` fjure, Remote/MCP-Lesezugriff ausschließlich Ziel; Fine-grained PAT-Scope vom Nutzer bestätigt, Token nie gelesen |
| Labelschreibrechte | PASS: 2 autorisierte Labels erstellt, 12 insgesamt via `list_label` zurückgelesen |
| Issue-Schreiben/Read-back/Mapping | PASS: 23 echte Issues über offiziellen MCP erstellt und einzeln sowie repositoryweit zurückgelesen; Marker/Titel/Labels/Inhalte/Dependencies/Status eindeutig, SHA256-Mapping vollständig; zweiter Abgleich 23 unverändert, 0 Duplikate |
| Quellen/Versionen | Datiert und gepinnt in technology-baseline.md; Pythonresolver-Dry-Run 53 Pakete auf 3.13.6 konfliktfrei, keine Installation; Linux/3.13.16-Laufzeitcheck später |
| Keine Anwendung | PASS: nur Specs/Contracts/Doku/Prüftooling; generierter Client nur ignorierter temporärer Prüfoutput; kein Commit/Push |

## Blocking

Keine aktuellen DoR-Blocker. Die frühere Formularübergabe wurde durch GitHubs dokumentierten Header `X-MCP-Features: mcp_apps_disable_form_deferral` behoben und nach Nutzerneustart dynamisch durch echte Writes/Read-backs bestätigt. Codex writes/prompt, PAT-Referenz, Repository-Scope und 15-Tool-Allowlist bleiben erhalten. Weder REST/gh noch vorgetäuschte UI-Submitflags verwendet. Mappingstatus `synchronized-read-back-verified`, 23 Einträge und keine pending IDs. Unabhängiger lokaler Review bestätigt Hashes/Markers/Labels/Dependency-URLs und offene Source-Checkboxen.

## Sichtbare Annahmen und nichtblockierende Einschränkungen

- Einzelner lokaler Supervisor, Loopback, cash-only PAPER, LONG/FLAT; reguläre US-Aktiensession, Krypto erst nach bestätigter Asset-/Kontofähigkeit. Kein LIVE.
- `SPY`/`QQQ` als kleines geplantes Defaultuniversum, `BTC/USD` optional; keine Accountberechtigung ohne spätere Laufzeitabfrage behaupten.
- Jev-Modellalias providerManaged; Antwortmodell/Question/Prompt/Inputversion aufzeichnen; deterministischer Replay nutzt gespeicherte Antworten. Neue Vendoraufrufe sind Vergleich, nicht deterministisches Replay.
- Simulationslimits 1 Position/Instrument, 2 insgesamt, 10% Notional und 1% Tagesverlust; konfigurierbar, auditiert und keine Anlageempfehlung.
- Kein Main-Commit/Basisvertrag: Initialcontract statt erfundenem Breakingdiff. Runtime-Contracttests und echte Broker-/Jev-Zertifizierung sind spätere Tasks, kein fälschlich bestandenes DoR-Runtimegate.
- Offizielle OpenAPI-Generator-3.1-Unterstützung ist Beta; nur konkrete verwendete Features werden empirisch geprüft. OTel/Playwright haben beta-markierte upstream Transitive; keine ausschließlich stabile Gesamtdependencyliste behaupten, späterer Dependencyreview notwendig.
- Image-Digests und vollständige App-Locks werden in PTV-01 auf Zielplattform fixiert; es werden jetzt keine Anwendungspakete/Images installiert.

## Nächster Auftrag

Der Planungs-/Sync-Auftrag ist abgeschlossen. Für die Implementierung lautet der gesonderte Folgeauftrag `$openspec-apply-change paper-trading-vertical-slice`. Er beginnt mit PTV-01; erster Meilenstein bleibt vollständig lokal mit FakeBroker/Mock. Task- und Issue-Status werden in jedem Apply-Durchlauf gemeinsam aktualisiert. DoR ist keine Runtime-Safetyzertifizierung und keine Freigabe für Agentenorders oder LIVE.
