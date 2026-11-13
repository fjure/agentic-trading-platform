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
