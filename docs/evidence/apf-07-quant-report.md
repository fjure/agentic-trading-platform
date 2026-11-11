# APF-07 · Berichte aus Quant-Evidenz

Der Berichtsdienst verwendet den vorhandenen `AgentReportV1`-Vertrag. Eine verbrauchte Betreiberfreigabe bindet den Bericht an die gespeicherte Ideen-, Hypothesen- und Definitionsversion sowie an den Batch und sein Manifest. Der Bericht verwendet für `subjectId` die UUID des gespeicherten Quant-Phasenlaufs. So adressiert der Feldpfad das konkrete Kandidaten- oder Referenzergebnis, auch bei wiederholter Auswertung desselben Kandidaten in mehreren Folds.

| Anforderung | Nachweis |
| --- | --- |
| APF-Q05 | Der Report übernimmt die bei Admission gebundene Quant-Historie. Der autorisierte Quant-Vergleich verwendet die vorhandenen Vergleichsregeln. Aktuelle Überschneidungswarnungen sind separat abrufbar, während der gebundene Registerstand erhalten bleibt. |
| APF-Q06 | [Quelltests](../../tests/unit/test_research_report_source.py) prüfen Bindungen und manipulierte Datensätze. [Vertragstests](../../tests/contracts/test_agent_report_claims.py) prüfen die vier Phasen, die Auswahl, exakte Dezimalwerte, die Referenzbindung, den Faktenbericht bei abgelehnter Interpretation und die Wiederholung nach einem konkurrierenden Commit. |
| APF-P05 | Die Vertragstests prüfen unbekannte Ergebnisreferenzen, freie Zahlen, aktive Inhalte, Toolvorschläge, falsche Richtungen und phasen- oder foldübergreifende Vergleiche. Das Audit speichert Berichts-ID, Version, stabilen Grund und Zeitpunkt. |
| E2E B/D · Backend | Der [PostgreSQL-Recovery-Test](../../tests/recovery/test_research_report_db.py) verwendet synthetische Daten, eine echte Agent-Admission und den bestehenden Quant-Worker. Er prüft das negative Holdout-Ergebnis, die Rollen, die einmalige Publikation, Reload, Rechteentzug, unveränderte historische Daten und Dump/Restore. |

Der Test verwendet keine echten Providerzugänge. Die Aufrufverdrahtung über Control API und Supervisor sowie die Anzeige in Guided und Advanced gehören zu APF-08 und APF-09. Aktuelle Überschneidungswarnungen liefert die Quant-Projektion. `currentRightsWarnings` im bestehenden Berichtsvertrag ist auf Quellenstatus beschränkt.
