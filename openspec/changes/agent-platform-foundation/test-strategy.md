# Abnahmeplan

APF-Abnahme nutzt echte FastAPI-/Relay-/PostgreSQL-/Angular-Wege und lokale LLM-HTTP-Doubles. Unit- und Propertytests decken Zustandsübergänge, Hashbindungen, Gate-Fehler und Zahlenprojektion vor Integration ab. Keine echten Providerzugänge und keine Brokerorders.

| ID | Testweg | Prüforakel |
| --- | --- | --- |
| E2E A | Angular → Control → Dialog → HTTP-Double → IdeaVersion → Quant Draft | Original getrennt, höchstens ein Run je Turn-Retry, Rückfrage, keine stille Strategieersetzung. |
| E2E B | Discovery oder Nutzeridee → Tool Policy → Draft → Validation → Betreiber → Admission → Worker → Report → Browserreload | Batch einmal, nur exakter Hash, Rollen getrennt, Ziffern aus realem persistiertem Toolresultat, Guided und Advanced identische Bindung. |
| E2E C | Neue synthetische Revision → Zeitplan → Quality/Trigger → HTTP-Double → Publikation → Dashboard | Genau ein Request und höchstens konfiguriertes Publikationslimit. Identische Wiederholung 0 neue Requests, 0 neue Ideen und 0 Experimente. DST, Neustart, Pause, Seitengrenzen und Retraktion zwischen Modellversand und Publikation separat. |
| E2E D | Experteninput mit Strategie, Parametern, Policy, Zeiten und Limits | Feldgleichheit bis Validierung, unsupported sichtbar, Approximation gesondert bestätigt. Wiederverwendung alter Fenster als abhängig markiert. |
| E2E E | Fehlende Daten, widerrufene Rechte, schlechte Qualität und fiktive Belege | Keine verbotene Ausleitung, keine fingierte Publikation, stabiler Grund und nachvollziehbare Idee ohne Start. |
| E2E F | Injection, Fake-Systemrolle, Credentials, fremdes Tool/URL, gefälschte und stale Approval, manueller Startversuch eines gebundenen Drafts, Doppelaufruf, Provider-Timeout, Budgetlimit, Logout | Reject mit Reason Code und redigiertem Audit. Keine zusätzliche Wirkung. Broker-, Risk-, Execution- und LIVE-Gates unverändert. Separater Test für verspätete Permits nach Fence-/Policywechsel und Probes gegen globale Caps. DB-ACL-/Netztests und unter Forschungsdruck auf PostgreSQL, Connection Pools und Containern messbare PAUSE_NEW-/Reconciliation-Proben. |
| E2E G | Crash vor/nach Claim, Providerdispatch, Toolcommit, Quant-Admission, Checkpoint und Publikation | Stale Worker verworfen. Bereits gespeicherte Resultate wiederverwendet. Unbekannter Providerzustand konservativ verrechnet. Kein doppelter Batch, keine doppelte Publikation und kein verlorener Quellenfortschritt. |

Weitere Orakel: Isolation-Checkliste im Threat Model, Crash- und Budgetmatrix im Runtime-Design sowie Exposure-/Vergleichsregeln in Quant-Integrität. Vertragsprüfungen und Dashboardsichtbarkeit werden je Paket geprüft. Der letzte Abnahmeschritt prüft das Zusammenspiel. Diese Matrix ist geplant und weist keine bestandenen APF-Laufzeittests aus.
