# Teststrategie und Traceability

Planung vom25.10.2026. Die folgende ursprüngliche Matrix dokumentiert Prüfziele.
Die tatsächlich implementierten konsolidierten Testpfade, alle31Szenarien und
ausgeführten Ergebnisse stehen im [Implementierungsnachweis](../../../docs/evidence/mdr-implementation-verification.md).
Insbesondere bündeln `test_research_storage.py` und seine PGhelper die ursprünglich
getrennt geplanten Integrationssuiten; Fortsetzung und gemeinsamer Idempotenznamensraum
sind zusätzlich durch T-HMD-013/014 und den echten Browser geprüft.

| Requirement | Szenarien | Tasks | Geplante fachliche Prüfevidenz |
|---|---|---|---|
| REQ-HMD-001 | T-HMD-001, T-HMD-002 | MDR-01,02,05 | `tests/unit/test_research_models.py`: exakte Decimalstrings, UTC, Symbolwechsel/Überlappung; echte PG-Constraints |
| REQ-HMD-002 | T-HMD-003, T-HMD-004 | MDR-03 | `tests/integration/test_research_vertical.py`: lokaler echter HTTPprovidertransport, Pagination, Persistenz zu Query; 401/403 statt Dummy, unbekannte/gesperrte Datenrechte verhindern bereits Provider-I/O |
| REQ-HMD-003 | T-HMD-005, T-HMD-006 | MDR-04 | `tests/recovery/test_research_ingestion.py`: Crash vor/nach atomarem Commit, Takeover/Fencing, Revision und doppelte Seiten |
| REQ-HMD-004 | T-HMD-007, T-HMD-008 | MDR-03,04 | `tests/integration/test_research_jobs.py`: 429/5xx/Timeout, Retry-After, Budget/Tokenzyklus/Größenlimit |
| REQ-HMD-005 | T-HMD-009, T-HMD-010 | MDR-05 | `tests/unit/test_research_quality.py`, `tests/integration/test_research_coverage.py`: DST, Feiertag, Earlyclose, unbekannter Kalender, OHLC-Konflikt |
| REQ-HMD-006 | T-HMD-011, T-HMD-012 | MDR-02,03,04,07 | `tests/security/test_research_provider.py`, echte PG-/Deploymentnegativen, unveränderte Ledger/Aktivierung und Credentialcanaries |
| REQ-REV-001 | T-REV-001, T-REV-002 | MDR-01,02,03,06 | `tests/unit/test_research_models.py`, `tests/unit/test_research_sources.py`: kanonischer Fingerprint, Schema-/Quellrevision, Qualität getrennt von Konfidenz |
| REQ-REV-002 | T-REV-003, T-REV-004 | MDR-02,03,06 | `tests/unit/test_research_pit.py`: heutiger Backfill vor früherem Cutoff ausgeschlossen, exakte Available/Ingested/Ende-Grenzen |
| REQ-REV-003 | T-REV-005, T-REV-006, T-REV-009 | MDR-02,05 | `tests/integration/test_research_storage.py`: Original vor späterer Revision stabil; Rückkorrektur A → B → A mit gleichem Inhaltsfingerprint und neuem Revisionsvorkommen, sofortige Wiederholung bleibt idempotent; Provider-asof keine Vintage; Splitadjustierung ohne Faktoren abgelehnt |
| REQ-REV-004 | T-REV-007, T-REV-008 | MDR-01,02,06,07 | `tests/integration/test_research_context_pit.py`, `tests/integration/test_research_api.py`: IDlookup ohne PIT-Bypass, Cursor an Filter/committed High-water-Mark gebunden, spätes Commit zwischen zwei Seiten |
| REQ-RCX-001 | T-RCX-001, T-RCX-002 | MDR-01,06,07,08 | `tests/integration/test_research_context_pit.py`: gleicher Cutoff für Bars/Referenzen/Kennzahlen, fehlende Quellen sichtbar |
| REQ-RCX-002 | T-RCX-003 | MDR-06 | `tests/unit/test_research_context.py`: unabhängig berechnete Rendite-/Volatilitätsfälle, mindestens drei Bars, fehlende Intervalle |
| REQ-RCX-003 | T-RCX-004 | MDR-01,06,07 | `tests/unit/test_research_sources.py`: echte Barsfähigkeit versus NOT_IMPLEMENTED, keine erfundenen News/Fundamentals/Makro |
| REQ-RCX-004 | T-RCX-005, T-RCX-006 | MDR-01,03,04,07,08 | `tests/security/test_research_api.py`: Auth/Origin/CSRF/Zeitraum/Größenlimit, idempotenter202-Auftrag, Payloadkonflikt409 und keine Seiteneffekte durch GET |
| REQ-RCX-005 | T-RCX-007 | MDR-08 | `tests/e2e/research.spec.ts`: Browser→echte API→PG, Import/Retry/Teilzustand/Reload, echte Datenlücken und erhaltene Paper-/Demoansichten |
| REQ-RCX-006 | T-RCX-008 | MDR-04,05,07 | `tests/integration/test_research_jobs.py`: Account- und Hostgesamtlimit mit Tradingkonkurrenz/Entitlement/Retry-After, kein Slot während Safetyarbeit, SQLlock erst nach Provider-I/O und begrenzte Researchlast bei weiterlaufendem Kill/Pause/Reconciliation; Trace-/Metrikredaktion |

## Prüfebenen

- Domain-/Propertytests prüfen Symbol-/Zeit-/Decimalgrenzen, kanonische Inhalte und PIT-Invarianten unabhängig vom Repositoryadapter.
- Providerprüfung vollständig offline mit lokalem HTTPdouble und realem Produktionsrequestcode. Keine Fixtures als angeblich echter Produktivadapter ausgeben. Echte persönliche Zugänge werden weder ausgelesen noch von Agenten getestet.
- Persistenzprüfung gegen isolierte tatsächliche PostgreSQL-Testdatenbank, reale Migration und Rollen; keine bestehende Nutzer-/Handelsdatenbank verändern. Update-/Deleteverbote, Grants und Jobfencing durch negative SQLprüfungen belegen.
- Integration durchläuft HTTPproviderdouble → Normalisierung → SQL → Evidenz → authentifizierte Query. Kein Mock ersetzt die eigene API/Storageimplementierung. Fehler-/Crashstellen vor und nach Commit explizit injizieren.
- Contract-first: kanonisches OAS/JSONSchema/Beispiele zusammen, bestehende Validatoren und tatsächlicher semantischer Diff zur main-Baseline, zweimal gleiche Clientgenerierung und TypeScript/Angularbuild; generierte Dateien nicht manuell ändern. Exact-route-Checks bleiben erhalten und werden nach MDR-07 gemeinsam erfüllt.
- Gesamtregression: passende bestehende Unit/Contract-/Jev-/Risk-/Broker-/Recovery-/Security-/Browserketten aus CI. Kein mehrfaches Addieren überlappender Testzahlen. Striktes OpenSpec und unabhängiger Safety-/Verifyreview.

## Ready und Done

Ready verlangt vollständige Proposal-/Spec-/Design-/Taskartefakte, acyclische Dependencies, vollständige Anforderungs-/Szenarienzuordnung, validierte Spezifikation und eindeutige neun Issuezuordnungen mit aktuellem Read-back. Ein gesonderter Nutzerauftrag startet Apply. Unbekannte persönliche Providerrechte verändern den implementierbaren Offline-/Produktionsadapterumfang nicht; tatsächlicher Abruf bleibt sichtbar UNVERIFIED/BLOCKED.

Done verlangt alle neun Tasks tatsächlich erfüllt, jeweilige Tests/Docs und geprüfte Gitintegration, unveränderte Safetygrenzen, echte nutzbare API/UI und persistente Researchdaten, redigierte Evidenz und aktuelle Issue-Synchronität. Ein fehlender Zugang oder Rechtebeleg ist kein erfolgreicher Providerabruf. Spec-Sync und Archivierung erst nach vollständiger Verifikation; keine Archivierung dieses geplanten Changes.
