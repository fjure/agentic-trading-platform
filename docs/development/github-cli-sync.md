# Autorisierter GitHub-CLI-Sync

Der Nutzer hat am2026-10-13 ausdrücklich die bereits installierte GH CLI als Alternative zum fehlenden offiziellen GitHub-MCP freigegeben. Diese Freigabe ersetzt ausschließlich die ältere Transportbeschränkung. Root-AGENTS.md hat Vorrang vor den noch MCP-zentrierten Schritten1/6/8 des github-issue-sync-Skills. Alle fachlichen Grenzen bleiben bestehen.

Verifiziert: gh2.100.0, gh api user liefert ausschließlich den Login fjure; gh repo view bestätigt fjure/agentic-trading-platform und defaultBranch main. Vor jeder Mutation alle Issues lesen, anhand Marker/Mappings/Task-ID eindeutig abgleichen und PR-Objekte ausschließen. Ausschließlich vorhandene Task-Issues erstellen/aktualisieren/schließen, Labels nur erstellen/aktualisieren; keine Kommentare, PRs, native Hierarchien, Milestones oder anderen Repositorymutationen über gh. Git-Branches/Push/Merge folgen dem gesondert autorisierten Git-Ablauf.

Issue-Bodies über eine UTF-8-JSON-Datei an gh api --method PATCH --input übergeben. Der Zielendpunkt muss exakt repos/fjure/agentic-trading-platform/issues/NUMMER sein. Nur den einen verwalteten Inhaltsblock ersetzen; Bytes außerhalb, manuelle Labels und Kommentare erhalten. Abschluss nur bei geprüfter Sourcecheckbox plus Evidenz, state_reason=completed. Direkten Einzel-Read-back und anschließend repositoryweiten Abgleich durchführen. Aktualisierten SHA256 des Managedblocks, Dependencies, Status und UTC-Zeit versioniert speichern. Idempotenter Wiederholungslauf darf keine unnötigen Writes erzeugen.

Keine Token-, Credential- oder Prozessumgebungswerte lesen; gh nutzt seine vorhandene private Anmeldung. Keine globale MCP-Registrierung oder Credentialänderung. MCP-Verifikation bleibt historische Evidenz; aktuelle CLI-Verifikation wird als eigener Transport dokumentiert.

Offizielle Referenzen, abgerufen2026-10-13 über Context7: [gh api und strukturierte Eingabedateien](https://cli.github.com/manual/gh_api), [gh issue list](https://cli.github.com/manual/gh_issue_list), [gh issue edit und body-file](https://cli.github.com/manual/gh_issue_edit). CLI-Version lokal geprüft, keine Installation oder globale Aktualisierung.
