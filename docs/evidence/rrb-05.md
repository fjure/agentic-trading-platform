# RRB-05 / #73: snapshotgebundener Reliability Report

Stand 28.10.2026. Apply freigegeben; Basis main `8a49382657f3108ff7a709a24234ef15a3692118`, Issuebranch `codex/ptv-73-reliability-report`. RRB-02 ist integriert. Reiner Assessor ohne Provideraufrufe, Simulation oder SQLwrites.

## Verhalten

REQ-RRB-008..012, T-RRB-013..020: sechs getrennte Dimensionen INTEGRITY/CALENDAR/CORPORATE_ACTIONS/HISTORICAL_AVAILABILITY/FEED/LICENSE, ausschließlich BLOCKED oder EXPLORATORY_WITH_LIMITATIONS, keine gewichtete Note. Alle festgelegten Einschränkungen bleiben sichtbar. Unbekannte Maßnahmen erzeugen keinen NO_ACTIONS-Beleg; die Prüfung liest konkrete statement.actions. Bekannte strukturelle Maßnahmen, Konflikte, fehlende notwendige Bars, unplausible OHLCV, gemischte Serien und der inklusive absolute 20-Prozent-Sprungfilter blockieren. Bardividenden werden ausdrücklich ausgeschlossen, nicht als Split behandelt.

Kalendergebundene Warm-up-/Trainings-/Testzählungen haben bei unbekanntem Kalender keinen erfundenen Nenner; abhängige Prüfungen bleiben NOT_EVALUATED. Unverifizierte historische Verfügbarkeit bleibt UNKNOWN/NOT_PROVEN. Lizenz UNKNOWN erzeugt eine Einschränkung, BLOCKED wird DENIED; aktueller negativer Status und nicht verfügbarer aktueller Port verhindern positive Zulassung, ohne historische Reports zu ändern.

Vorschau und Finalreport verwenden dieselbe reine Regel. Vollständige kanonische Übersicht über to_dict; Findings/Revisionsreferenzen als unveränderliche paginierbare Einträge. Ein positiver Report benötigt ein voll gebundenes Manifest. Fehlendes Manifest erzeugt REFERENCE_MISSING; gebundene alte Heads dürfen nicht mit Diagnosen neuerer Revisionen kombiniert werden. Der semantische Hash bindet Protokoll/Snapshot/Build/Trust/Befunde/Receipt und schließt die zur Projektion übergebenen Laufzeit-IDs/Phase sowie aktuelle Warnungen aus; Persistenz fingerprintet zusätzlich den vollständigen publizierten Body.

## Ausgeführte Evidenz

- Autor: 44 Reliabilitytests plus retrospektive Vertragsmatrix, **332 passed**; Ruff und Formatprüfung bestanden.
- Unabhängiger Review nach finaler Manifestkorrektur: **44 passed**, keine offenen P1/P2.
- Hauptagent auf eingefrorenen Quellen: Reliability und vier unveränderte Strict-Hashorakel, **46 passed** (1.99s); Ruff sauber.
- Zusätzlich nach Wiederaufnahme: **173 passed** bestehende Backtest-/Readiness- und retrospektive Dataset-/Simulator-Domainregressionen; dieser Lauf enthält den damals noch nicht eingefrorenen Assessor ausdrücklich nicht.

Die Matrix verwendet unabhängig berechnete Decimalgrenzen 19.999/20/20.001 Prozent in beide Richtungen, einschließlich Overnight; konkrete DST-/Feiertag-/Earlyclose-Kalenderzählungen; Splits/Reverse-Splits/Spin-offs/Dividenden und Widersprüche; unbekannte/verweigerte Rechte und Portausfall; fehlende Bars/Referenzen, Budgetabbruch/Checkpoint und gleiche Snapshots. Vollständige Reportausgaben werden gegen die kanonischen Verträge geprüft.

Zeilen-, Maßnahmen-, Finding- und Publikationsbudgets verhindern positive Teilauswertung. Bei Budgetabbruch bleiben ungeprüfte Dimensionen NOT_EVALUATED; der Vertrag bewahrt separat tatsächliche UNKNOWN-Aussagen für Lizenz und historische Verfügbarkeit. Keine Rendite, Datenweitergabe, persönliche Nutzungsrechte oder Handelsfreigabe werden damit bestätigt. Reale Worker-/HTTP-/Browser-/Lastabnahme folgt RRB-06..08.
