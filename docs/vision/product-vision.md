# Produktvision

## Zielbild

Agentic Trading Lab soll langfristig eine vollständig autonome, lokal betriebene Multi-Agenten-Handelsorganisation werden. Menschen definieren Auftrag, zulässige Märkte, feste Risikogrenzen und die jeweils erlaubte Autonomiestufe. Agenten übernehmen spezialisierte Aufgaben und koordinieren einen nachvollziehbaren Strategiezyklus. Die aktuelle Priorität liegt auf Research, Paperbetrieb und belastbaren Sicherheitsgrenzen; LIVE ist derzeit immer technisch unerreichbar.

Das Zielbild umfasst klar abgegrenzte Rollen: **Market Intelligence** sammelt und bewertet Markt-, Kalender- und Nachrichtenkontext; **Quant Research** untersucht Hypothesen und historische Daten; **Portfolio Planner** verbindet freigegebene Strategien unter Portfolio- und Risikogrenzen; **Supervisor** überwacht Laufzeiten, Freigaben, Fehler und Eskalationen. Risk und Execution bleiben unabhängig: Risk prüft deterministisch, Execution setzt nur für die aktuelle Autonomiestufe zulässige Aktionen um. Das Dashboard macht Quellen, Ergebnisse, Grenzen und offene Betreiberentscheidungen sichtbar. Heute existieren getrennte Worker-, Execution-, Control- und Watchdog-Laufzeiten sowie ein Jev-Shadowpfad, aber diese vollständige Rollenorganisation ist noch nicht implementiert.

## Märkte und Autonomie

Langfristig soll die Plattform Aktien und ETFs, Kryptowährungen, Devisen, Rohstoffe, Indizes sowie Futures und weitere Derivate untersuchen und – soweit ausdrücklich erlaubt – handeln können. Das sind Ambitionen, keine heute verfügbaren Produktfähigkeiten. Die bestehende Anwendung deckt ein enges Universum ab, vor allem SPY und QQQ sowie begrenzte Krypto-Pfade. Kalender, Handelszeiten, Datenrechte, Instrumentregeln und Risikomodelle müssen je Marktklasse gesondert belegt werden.

Die Autonomiestufen beschreiben die erlaubte Handlung:

- **0 – Research und Beobachtung:** Daten analysieren und Ergebnisse zeigen; keine Orders.
- **1 – Empfehlung:** Vorschläge erstellen; jede Aktion braucht menschliche Freigabe.
- **2 – Kontrolliertes Paper:** innerhalb einer ausdrücklich aktivierten Paperfreigabe automatisch simulierte Brokeraktionen ausführen.
- **3 – Begrenztes LIVE:** erst nach gesonderter ausdrücklicher späterer Autorisierung, innerhalb festgelegter Grenzen.
- **4 – Erweitertes autonomes LIVE:** größerer Handlungsspielraum innerhalb unveränderlicher Risikogrenzen; ebenfalls nur nach eigener Autorisierung.

Heute ist LIVE für alle Stufen technisch unerreichbar. Diese Definition macht weder Stufe 3 noch 4 verfügbar oder freigegeben.

Der Strategiezyklus soll Datenaufnahme, Qualitätsprüfung, Research, Portfolioplanung, unabhängige Risikoprüfung, Replay oder Simulation und Ergebnisanalyse verbinden. Ein versioniertes Strategieregister soll Status und Lebenszyklus von experimentellen Hypothesen bis zu freigegebenen Varianten abbilden. Promotion, Rücknahme und Rollback benötigen nachvollziehbare Kriterien, Out-of-sample- und Walk-forward-Auswertung sowie dokumentierte Experimente. Agenten dürfen weder Produktionscode selbst verändern noch Strategien automatisch in LIVE befördern. Jev ist heute ein Shadow-Entscheider mit Abstention, kein autonomer Brokeragent. LangGraph ist nicht eingeführt.

## Bedienung und Nachvollziehbarkeit

Das Dashboard soll Märkte, Datenfrische und -lücken, Kalender, Strategien, Replayvergleiche, Risikogrenzen und Auditspuren zusammenführen. Der Betreiber muss erkennen können, welche Datenbasis und Version eine Aussage stützen und warum ein Schritt blockiert wurde. Heute zeigt das Dashboard bereits Betriebsstatus und Markt-, Entscheidungs-, Risiko-, Order- und Auditverläufe für die vorhandenen Abläufe; Research- und historische Datenansichten sind geplant.

Diese Vision ergänzt die fachlichen OpenSpec-Anforderungen nicht. Konkretes Verhalten und Abnahmekriterien gehören in jeweilige Changes und Spezifikationen; dauerhaft geltende Sicherheitsregeln stehen in [AGENTS.md](../../AGENTS.md). Implementierungsstand und Nachweise sind in den verlinkten OpenSpec-Changes und [Evidenzdokumenten](../evidence/) zu finden.
