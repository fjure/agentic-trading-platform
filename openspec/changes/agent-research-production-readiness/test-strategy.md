# Teststrategie

| Schicht | Positiver Nachweis | Negativer Nachweis |
| --- | --- | --- |
| Wire | echte Requestform je Provider, valide Usage und Ergebnis | Modellwechsel, fehlende Usage, Refusal, doppelte JSON-Felder, Toolcalls, Oversize, Schemaabweichung |
| Egress | reales TCP/TLS im abgeschotteten Deployment | falsches SNI/CA, Redirect, DNS-Rebinding, IPv4/IPv6 interne Ziele, Proxyumgebung, Timeout und Slow Read |
| Setup/Privacy | Setup, Rotation, neue Bindung, exakte Preview, separater Commit | CSRF, Schreibrechte, Symlinks, alte Generation, Datenklasse/PII, stale Preview, Rücknahme am Sendepunkt |
| Budget/Recovery | konkurrierende atomare Reservierung, einmalige Settlement-Transaktion | unknown, doppelter Attempt, Crash nach Sendestart, 429/5xx, fehlender Tarif, Überkosten, verspätete Antwort |
| Vertikal | Angular → Control → Supervisor → Gateway → Quant → PostgreSQL → Report → Angular, plus Discovery | falsches Approval, ungeklärte Approximation, direkter Discovery-Startversuch, Datenrechtsverlust, stale Fence, Doppelstart, lokale Klärung, deaktivierter Zeitplan |
| Isolation | Risk-Kill und Reconciliation unter definierter Researchlast | null Brokerorders/Finanzwrites, Broker-/Jev-Schlüssel und allgemeines Netz unerreichbar |
| NLP | versionierte Scorer-Orakel, unabhängig annotierte Referenzen | erfundene Evidenz, Parameteränderung, Prompt Injection, mehrdeutige oder untestbare Idee |
| Betreiber-Smoke | ein synthetischer strukturierter Roundtrip auf Produktionsroute | fehlende Autorisierung, falsches Modell, unbekannte Usage, Budgetüberschreitung, Isolation verletzt |

Der Smoke-Audit führt Profilrevision, Zweck, Provider, Modell, Versuch, Ergebnis, reservierten Betrag, Usage-Status, Dauer und UTC. Rohantwort, Anfrageinhalt und Credentials bleiben aus Audit und Logs. Ein Timeout nach möglichem Versand bleibt ein unbekannter Ausgang.

## Prüfzuordnung des Refinements

Die [normativen Szenarien](traceability.md) sind als eindeutige Pflichtfall-IDs für ihre isolierten Offline-Fixtures zu registrieren. Reale Smoke- und Holdout-Läufe werden separat als Betreiber-Evidenz erfasst. ARP-04 prüft konkurrierende Transaktionen, UTC-Wechsel, Budgetrevision, Unknown-Settlement und Überlauf am Ledger. ARP-08 prüft günstige und teurere Modellbindungen, Requestbeweis und Fallback, Zusatzkomponenten, fehlende/alte Tarife, fehlende Obergrenze, zu kleines Betreiberlimit, Modellabweichung, Widerruf vor und nach Commit, Replay, Timeout und unvollständige Usage. Jede Sperre belegt ausbleibenden neuen Versand. ARP-06 prüft die Auswertungsorakel für fehlende Antworten, Gruppen-/Fallnenner, Wilson-Beispiel, Regression in beiden Richtungen, Holdout-Leakage und Nulltoleranz. ARP-05 prüft Gate-Aggregation mit Skip, fehlender Umgebung, fehlenden Test-IDs und Artefakten sowie falschem SHA. ARP-10 belegt vollständige Offline-Suites, technischen Smoke und Fachfreigabe als getrennte Ergebnisspalten.
