## Purpose

Isoliert Providerzugang, Schlüssel und zulässigen Modellkontext vom Research- und Tradingbetrieb.

# Agent Provider Boundary

## ADDED Requirements

### Requirement: APF-P01 Getrenntes und standardmäßig gesperrtes LLM-Gateway
Die Plattform SHALL OpenAI und Anthropic über ein isoliertes, standardmäßig gesperrtes LLM-Gateway nach dem [Sicherheitsmodell](../../threat-model.md) anbinden. Für jeden Dispatch MUST es festen TLS-Egress, authentisierte Aufträge, aktuelle Supervisorzulassung und einen dauerhaften Einmalclaim pro Versuch erzwingen. Entzug und Zulassung SHALL serialisiert werden. Nach wirksamem Entzug bleiben mögliche Kosten gebunden und fachliche Ergebnisse gesperrt.

#### Scenario: APF-T-P01 Fehlende Freigabe und Umleitung
- **WHEN** ein Researchlauf ein für Research inaktives Profil, eine benutzerdefinierte URL, ein privates DNS-Ziel oder einen Redirect erhält
- **THEN** sendet das Gateway keine Anfrage an das Ziel und meldet einen stabilen redigierten Blockierungsgrund.

#### Scenario: APF-T-P02 Autorisierter Auftrag über HTTP-Double
- **WHEN** ein aktiver Lauf einen gültigen, noch nicht verwendeten Auftrag an ein freigegebenes Providerprofil sendet
- **THEN** verwendet das Gateway ausschließlich gebundenen Provider, Modellversion, feste HTTPS-Route und erlaubten Requesttyp, prüft das TLS-Zertifikat und gibt nur einen typisierten Ergebnisstatus und einen begrenzten Ergebniswert zurück.

#### Scenario: APF-T-P13 Wiederholter Gatewayauftrag
- **WHEN** zwei Aufrufer parallel oder nach einem Neustart dieselbe Versuchs-ID einreichen
- **THEN** ist höchstens ein externer Dispatch zulässig und ein Retry nach APF-R04 verwendet erst nach positivem Nachweis des Nichtversands eine neue Versuchs-ID mit eigener Prüfung.

#### Scenario: APF-T-P14 Noch nicht eingelöster Altauftrag
- **WHEN** ein bisher ungenutzter, formal gültiger Auftrag erst nach einem Fencewechsel, Run-Abbruch oder Entzug der gebundenen Rechte zur Dispatch-Zulassung vorgelegt wird
- **THEN** lehnt die aktuelle Autorität die Zulassung ab und es geht keine Provideranfrage ab.

#### Scenario: APF-T-P15 Rechteentzug nach Zulassung
- **WHEN** Pause, Fencewechsel, Abbruch, Rechte- oder Policyentzug eine bereits zugelassene Anfrage betreffen
- **THEN** versucht das Gateway den Abbruch, der Supervisor hält mögliche Kosten fest und der Lauf nimmt kein fachliches Ergebnis des Versuchs an.

### Requirement: APF-P02 Write-only BYOK und generationsgebundene Nutzung
Nach gesonderter Freigabe SHALL die Plattform lokale Same-Origin-Einrichtung mit flüchtiger Eingabe, Write-only-Transport und atomarer privater Ablage anbieten. Nur das Gateway MUST den Key dauerhaft konsumieren dürfen. Speichern, Test, Aktivierung, Rotation und Widerruf SHALL getrennte Betreiberaktionen sein. Die Aktivierung MUST eine geprüfte Generation binden und Widerruf sperrt ihren weiteren Einsatz.

#### Scenario: APF-T-P03 Sichere Eingabe und Rotation
- **WHEN** ein Betreiber einen synthetischen Schlüssel speichert, prüft, rotiert und die vorige Generation widerruft
- **THEN** bleibt der Wert nur für den zugelassenen flüchtigen Transport und Gatewaypfad erreichbar. Status, Browserpersistenz, Modellkontext, Datenbank, Git, Logs, Telemetrie, Diagnose und Audit bleiben schlüsselfrei. Ein alter Lauf kann weder die widerrufene Generation erneut verwenden noch die neue Generation übernehmen. Bereits versandte Aufrufe werden möglichst abgebrochen. Ein lokaler Widerruf wird getrennt vom gesondert bestätigten Providerwiderruf angezeigt.

#### Scenario: APF-T-P04 Missbrauch des Einrichtungsdialogs
- **WHEN** eine fremde Origin, eine abgelaufene Sitzung, doppelte Header oder ein ungültiger Body die BYOK-Verwaltung aufruft
- **THEN** wird die Anfrage vor Schreibzugriff und Providerdispatch abgewiesen, ohne ihren Inhalt wiederzugeben.

#### Scenario: APF-T-P16 Verbindungstest und Aktivierung
- **WHEN** ein Betreiber einen Schlüssel nur speichert oder nach gesonderter Egress- und Testfreigabe einen Verbindungstest anfordert
- **THEN** bleibt Speichern passiv. Der Test verwendet vor Researchaktivierung ausschließlich eine minimale synthetische Anfrage ohne Researchdaten mit separatem Budget und belegt nur die getestete Profil-, Modell- und Credentialgeneration.

### Requirement: APF-P03 Versionsgebundene Provideradapter und Fähigkeiten
Die Plattform SHALL pro Provider und Modell ein unveränderliches Capabilityprofil nach [ADR 02](../../adr/0002-provider-boundary.md) binden. Der Adapter MUST strukturierte Antworten lokal validieren und bestätigte Ablehnung vor Ausführung, akzeptierte Antwort, unbekannten Ausgang und Rate-Limit-Information unterscheiden. Ein Retry MUST Profil- und Budgetbindung wahren. Providergehostete Tools, stiller Providerwechsel und Schemaabschwächung bleiben gesperrt.

#### Scenario: APF-T-P05 Abweichende Fähigkeit und Modellidentität
- **WHEN** das gebundene Profil kein erforderliches Ausgabeformat unterstützt oder ein Double ein abweichendes Modell beziehungsweise eine unvollständige Antwort meldet
- **THEN** bleibt der Schritt blockiert oder schlägt typisiert fehl und es entsteht kein veröffentlichbarer Output oder alternativer Modellaufruf.

#### Scenario: APF-T-P06 Timeout und unvollständige Usage
- **WHEN** nach möglicher Providerannahme ein Timeout eintritt oder Usage fehlt
- **THEN** dokumentiert die Plattform den unbekannten Status, hält die konservative Reservierung nach Laufpolicy und behandelt fehlende Usage nie als null.

### Requirement: APF-P04 Nachweisbare Rechte für externen Modellkontext
Vor jedem externen Request SHALL eine deterministische Datenpolicy ausschließlich explizit zulässige und erforderliche Felder nach Datenklasse, Empfänger und Zweck freigeben. Fehlende, widersprüchliche, veraltete oder widerrufene Rechte MUST die Übertragung sperren. Secrets, Personenbezug, Handels- und Auth-Daten bleiben ausgeschlossen. Die Plattform SHALL die Entscheidung und ihre Policyversion ohne Payload auditieren.

#### Scenario: APF-T-P07 Lokal nutzbare, extern gesperrte Evidenz
- **WHEN** Marktdaten, Ableitungen oder Experimentresultate nur lokal zulässig sind, ein Profilwiderruf vorliegt oder die Übertragbarkeit von Dialogteilen und Zusammenfassungen unbekannt ist
- **THEN** gelangt kein betroffener Wert zum Gateway und der Workflow zeigt eine erklärbare Blockierung oder eine verfügbare lokale Präzisierung an.

#### Scenario: APF-T-P08 Minimaler externer Kontext
- **WHEN** der Betreiber ein Profil und eine geprüfte Datenklasse zur Übermittlung freigegeben hat
- **THEN** enthält der Providerrequest nur die für diesen Schritt benötigten zulässigen Felder, und die Anwendung speichert ausschließlich den zulässigen Referenznachweis.

### Requirement: APF-P05 Nicht vertrauenswürdige Inhalte und geschützte Publikation
Die Plattform SHALL Nutzereingaben, externe Daten, Toolresultate, Zusammenfassungen und Modelloutputs als untrusted Daten behandeln. Ausschließlich versionierte Serverinstruktionen bilden die Systemrolle. Die Plattform MUST Toolvorschläge, Belege und Outputs lokal prüfen, ungebundene Zahlen verwerfen und veröffentlichte Inhalte sicher darstellen. Ablehnungen SHALL stabile Reason Codes und redigierte, korrelierbare Auditereignisse erzeugen.

#### Scenario: APF-T-P09 Eingeschleuste Anweisung
- **WHEN** ein Dialog oder Toolresultat eine Systemrolle imitiert oder ein Modell einen Schlüssel, ein unbekanntes Tool oder eine nicht zum Run gehörende Ergebnis-ID verlangt
- **THEN** bleiben Autorität und Toolmenge unverändert und die Plattform verhindert Seiteneffekte und die Veröffentlichung falscher Belege.

#### Scenario: APF-T-P10 Reportmanipulation
- **WHEN** eine Modellantwort frei behauptete Kennzahlen oder aktive HTML-/Linkinhalte enthält
- **THEN** werden ungebundene Zahlen nicht als Ergebnisse übernommen und aktive Inhalte nicht ausgeführt.

### Requirement: APF-P06 Physische und fachliche Trennung von Research und Handel
Research, Gateway, LLM-Einrichtung und Quant-Tools MUST ohne Broker-/Jev-Credentials und ohne Netz-, DB- oder Toolrechte für Handel, Ledger, Risklimit oder Promotion bleiben. Quant-Approvals SHALL nur den gebundenen Researchstart autorisieren. Risk, Execution und Reconciliation MUST unabhängig bleiben. LIVE bleibt technisch unerreichbar. Exakte Finanzwerte SHALL Decimal und technische Zeitpunkte UTC verwenden.

#### Scenario: APF-T-P11 Rechteentzug im echten Deployment
- **WHEN** ein Researchprozess versucht, Brokerziele, Credentialmounts, Handelsdatenbankoperationen oder Risikokonfiguration zu erreichen
- **THEN** weisen die tatsächlich wirksamen Prozess-, Datenbank- und Netzwerkgrenzen die Operationen unabhängig von Prompt und Session ab.

#### Scenario: APF-T-P12 Researchlast bei laufender Safetyfunktion
- **WHEN** Provideraufrufe hängen, Kostenkontingente ausgeschöpft sind, Research die eigenen Prozess-, Queue- oder Datenbankgrenzen erreicht oder eine Research-Workerinstanz abstürzt
- **THEN** bleiben PAUSE_NEW, sicherer Neustart, deterministische Submitgates und Reconciliation innerhalb der bestehenden Sicherheitsanforderungen funktionsfähig.
