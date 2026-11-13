# Unabhängige Planungsreviews

Drei voneinander unabhängige Subagenten prüften lesend den Entwurf und den relevanten Quellstand. Laufzeit- oder Providerabnahmen waren kein Teil dieser Reviews.

| Feld | Befund | Eingearbeitete Korrektur |
| --- | --- | --- |
| LLM Provider Architecture | Zirkuläre Probe-/Aktivierungsgates und unvollständige reale Gateway-Komposition | STAGED-Probe-Grant, reale Purpose-RPCs und gemeinsamer Sendekern, dokumentierte Protokollversion und Usage-Semantik. Der Reviewer hält die Produktionsroute für technisch geeignet, beide First-Party-APIs zu erreichen. |
| Privacy & Credential Security | Gemeinsamer Setup-Pfad, fehlender Journal-Mount und Provider-Caching-Risiko | Separater Same-Origin-Pfad ohne Control-Transit, persistentes Gatewayjournal, Kontomodell und Aufbewahrung an Profil gebunden. |
| Runtime, Budget & Recovery | Race zwischen Entzug und Sendestart, ungesicherte Tokenobergrenze und UTC-Wechsel | Gemeinsame atomare Supervisor-Transition, konservatives Unknown vor I/O, maximale Modellinputgrenze, periodengebundene Abrechnung und sichere Journalepoche. |
| DevOps & CI Reliability | TLS-Gate hatte fehlende Abhängigkeiten und Harness setzt Dev-Netz voraus | ARP-05 automatisiert APF, ARP-09 setzt Produktions-TLS-Gate auf fertiger Komposition auf. CI-Provisionierung, ausgewählte Skips und Ruleset präzisiert. |
| Conversational UX | Statusdimensionen und erneute Freigabe unklar | Credential-, Probe-, Zweck-, Datenfreigabe- und Zeitplanzustand getrennt, kein Auto-Resend bei Reload oder Timeout. |
| Quant Research Integrity | Approximation und Discovery-Übernahme fehlten. Eval zählte nur übernommene Modellfehler. | Separates Approximationsgate, ausdrückliche Moduswahl, interaktive Übernahme und Bewertung von Rohfehlern plus unabhängiger Holdout. |
| Risk/Execution Isolation | Fehlende konkrete Latenz- und Grenzassertionen | Je unter fünf Sekunden für Pause, Kill und Reconciliation, null Brokerorders und abgewiesene SQL-/RPC-/Egress-Wege unter aktiver Last. |

## Refinement am Basisstand 9dbbb6a

| Unabhängiger Review | Befund und Auflösung |
| --- | --- |
| A · Provider und Kosten | OpenAI-Standardtier ausdrücklich binden und beim Settlement prüfen. Ursprünglichen Komponententarif erhalten und VERIFIED nur nach vollständigem, begrenztem Settlement aktueller Bindung projizieren. Nachprüfung bestanden. |
| B · NLP und Quant | Recall und fehlende Antworten präzisiert, Mindestschichtung und drei autorisierte Wiederholungen normativ ergänzt, unvollständige Vergleichspaare gesperrt und neue Baseline mit unabhängiger Bestätigung verlangt. Nachprüfung fachlich bestanden, Szenario E02-S06 anschließend unter das gleichnamige Requirement verschoben. |
| C · SDD und Testarchitektur | Anforderungen, Szenarien und Paketnachweise vereinheitlicht, CI-Fixtures von Betreiber-Evidenz getrennt, Manifestformat und Moduswechsel konkretisiert. Nachprüfung bestätigt die Korrekturen. Die abschließende Strukturprüfung prüft zusätzlich die Zugehörigkeit von E02-S06. |
