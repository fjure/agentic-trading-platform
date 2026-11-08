## Purpose

Koordiniert begrenzte, wiederaufnehmbare Researchläufe und bindet Quant-Zulassung an eine Betreiberentscheidung.

## ADDED Requirements

### Requirement: APF-R01 Geschlossenes Agentenregister
Das System SHALL ein geschlossenes Register unveränderlicher Agentenversionen betreiben und je Lauf Version und Policy binden. V1 SHALL `quant-research-agent-v1` auf Forschungsstufe 0 ausliefern. Fehlende oder widerrufene Einträge und neu verschärfte globale Sperren MUST weitere betroffene Schritte blockieren.

#### Scenario: Versionierter Registereintrag
GIVEN eine ausgelieferte Agentenversion WHEN der Supervisor sie bindet THEN enthält der Eintrag ID, Version, Rolle, Beschreibung, Systeminstruktionsversion, Provider-/Modellbindung, Tool-ID/-Version-Liste, versioniertes Ausgabeschema, Zeit-, Token-, Kosten-, Modellaufruf-, Schritt- und Toolaufruflimits, Autonomiestufe, Abstentionsregeln und Implementierungsfingerprint. Eine neue kompatible Konfiguration gilt für künftige Läufe.

#### Scenario: APF-T-R01 Modell schlägt Rechteänderung vor
GIVEN ein gültiger Research-Run WHEN Modelloutput einen neuen Provider, Prompt, ein neues Tool, höhere Autonomie oder das Laden von Programmcode oder Plugins fordert THEN bleibt die Bindung unverändert, der Supervisor unterbindet den Zugriff und auditiert `AGENT_POLICY_DENIED`.

### Requirement: APF-R02 Dauerhafter Lauf und deterministischer Supervisor
Der Supervisor SHALL Aufträge dauerhaft, idempotent und mit unveränderlichen Eingaben verwalten. Nur definierte Übergänge aus [Laufzeit und Budgets](../../runtime-budget.md) SHALL möglich sein. Zustandswechsel, Audit und Folgearbeit MUST gemeinsam committen.

#### Scenario: Persistenter Supervisorstatus
GIVEN ein angenommener Lauf WHEN der Betreiber dessen Status liest THEN sind Run-ID, Auftraggeber, Schlüssel, Auftragshash, Dialog-/Ideen-/Quellenverweise, Agenten-/Policy-/Modellbindung, Trace, Zustandsversion, UTC-Fristen, Lease/Fence und versionierte Checkpoints gespeichert. Die paginierte Ansicht referenziert Status, Tools, Providerzustand, Budget, Freigaben, Abbruch, Blockierungsgründe und Bericht. Auf Nutzer oder Experiment wartende Läufe halten keine Workerlease.

#### Scenario: APF-T-R02 Doppelte Laufanlage
GIVEN zwei gleichzeitige Anfragen für denselben Schlüssel WHEN der gespeicherte Auftragshash übereinstimmt THEN existiert ein Lauf mit genau einem initialen Job und beide Aufrufer erhalten dieselbe ID. Bei abweichendem Hash wird die spätere Anfrage als `IDEMPOTENCY_CONFLICT` abgewiesen.

#### Scenario: APF-T-R03 Alte Fence
GIVEN ein Worker hat seine Lease verloren WHEN er einen Checkpoint oder Bericht veröffentlicht THEN scheitert die Transaktion mit `LEASE_LOST` und kann weder den Lauf ändern noch weitere Arbeit anlegen.

### Requirement: APF-R03 Typisierte Tools und Laufgrenzen
Das System SHALL ausschließlich registrierte Researchreads, Ideen-/Draftschritte, Quant-Validierung und Resultatreads erlauben. Quant-Starts MUST APF-R05 folgen. Broker-, Risk-, Credential-, Shell-, Git-, Docker- und freie HTTP-Tools MUST unerreichbar bleiben.

#### Scenario: Autorisierung und Outputprüfung
GIVEN ein registrierter Aufruf WHEN er ausgeführt werden soll THEN prüft die serverseitige Policy zuvor Agent-/Run-ID, Agent-/Tool-/Policy-Version, aktuelle Fence, Berechtigung, Besitz und Herkunft von Referenzen, Eingabeschema, Werte-/Größenlimits, Frist, Budget und Schrittidentität. Das System prüft Schema und Größe des Outputs, speichert ein referenzierbares Resultat und auditiert Toolregisterbezug, Entscheidung, Aufrufhash, Ergebnisstatus, Trace und Reason Code. Modellnamen und Fremdtexte verleihen keine Autorität.

#### Scenario: APF-T-R04 Manipulierter Toolaufruf
GIVEN ein Tooloutput enthält einen Credentialbefehl, eine fremde Run-ID oder einen frei erfundenen Toolnamen WHEN das Modell daraus einen Aufruf vorschlägt THEN wird vor der Wirkung ein stabiler Ablehnungsgrund protokolliert und es gibt keinen externen Zugriff.

#### Scenario: APF-T-R05 Wiederholte Toolaktion
GIVEN ein erfolgreich gespeichertes Toolresultat WHEN der Aufruf mit derselben Identität und demselben Eingabehash wiederkehrt THEN erhält er genau dieses Resultat; bei anderem Hash wird er abgelehnt.

### Requirement: APF-R04 Sichere Wiederaufnahme und Abbruch
Jeder Modellversuch MUST vor externem I/O mit Versuchsschlüssel, Request-Hash, UTC-Frist und Dispatchstatus Budget reservieren. Dispatchautorisierung und Ergebnisannahme SHALL aktuelle Fence und gültigen Laufzustand prüfen. Nur nach verifiziertem Nichtversand MAY ein begrenzter Retry stattfinden. Für externe Dialoginterpretation gilt zusätzlich APF-R07.

#### Scenario: Alte Dispatcheinladung und Sessionverlust
GIVEN die Einladung zum Dispatch wurde vor einem Abbruch, einer Pause oder einer Leaseübernahme erstellt WHEN sie erst danach am Gateway eintrifft THEN scheitert die private Zulassungsprüfung gegen die aktuelle Fence oder Epoche. Eine verlorene Sitzung sperrt neue authentifizierungspflichtige Nutzeraktionen; bereits angenommene begrenzte Hintergrundarbeit bleibt an die Run-Policy und ein angenommener Quant-Batch an seine eigene Laufzeit gebunden.

#### Scenario: APF-T-R06 Timeout nach Verarbeitung
GIVEN der HTTP-Double hat einen Modellrequest erhalten WHEN Antwort oder Usage verloren gehen und der Worker neu startet THEN wird keine zweite Berechnung angefordert, die Kosten werden als unbekannt angezeigt und die Reserve bleibt gebunden.

#### Scenario: APF-T-R07 Absturz nach persistiertem Ergebnis
GIVEN das validierte Ergebnis eines Versuchs ist gespeichert WHEN der Worker vor dem nächsten Schritt stirbt THEN nimmt der Nachfolger nach Leaseübernahme am Checkpoint auf und nutzt das persistierte Ergebnis.

### Requirement: APF-R05 Einmalige Operatorfreigabe und atomare Quant-Admission
Nur der authentifizierte, autorisierte lokale Betreiber SHALL einen agentengebundenen Quant-Start ausdrücklich freigeben. Bindungs- und Budgetprüfung, bestehende Quant-Admission und Freigabeverbrauch MUST atomar erfolgen. Agentische Entwürfe behalten ihre Herkunftsbindung über Revision und Ansichtswechsel; unabhängige manuelle Abläufe bleiben verfügbar.

#### Scenario: Gültige konkrete Freigabebindung
GIVEN ein genehmigter Start WHEN die Admission entscheidet THEN prüft sie Run-ID/Zustandsversion, Betreiber, Aktion, Ideen-/Hypothesenversion, gegebenenfalls akzeptierte Approximation, Draft-ID/-Version/-Hash, Validation-ID/-Hash, Research Mode, Ressourcenbudget, aktive Policies und Ablaufzeit. Geänderte Bindung, blockierter Lauf, fehlende Sitzung, Fristablauf oder Policywiderruf verhindern die Admission. Agenten und proaktive Läufe haben kein Startrecht.

#### Scenario: APF-T-R08 Doppelte Freigabe
GIVEN eine gültige Freigabe WHEN zwei gleichzeitige Requests denselben Start mit identischem Schlüssel beantragen THEN wird genau ein Quant-Batch angenommen, die Freigabe genau einmal verbraucht und ein Replay zeigt dieselbe Batch-ID.

#### Scenario: APF-T-R09 Alte Freigabe
GIVEN eine Freigabe für einen früheren Draft, einen abgelaufenen Dialog oder eine andere Policy WHEN ein Start angefragt wird THEN erhält der Betreiber einen erklärbaren Ablehnungsgrund und weder Budget noch Batch werden verbraucht.

#### Scenario: APF-T-R10 Admission scheitert
GIVEN eine sonst gültige Freigabe WHEN das Quant-Lab seine gemeinsame Slotgrenze erreicht THEN werden weder Batch noch Approvalverbrauch persistiert; ein neuer expliziter Betreiberrequest muss alle Bedingungen erneut prüfen.

#### Scenario: APF-T-R13 Agentendraft im manuellen Startpfad
GIVEN der Agent hat einen Entwurf mit persistierter Laufbindung vorbereitet WHEN ein Request diesen Entwurf ohne passendes Approval an den bisherigen Quant-Startpfad übergibt THEN wird der Start auch bei gültiger Betreibersitzung vor der Admission mit `APPROVAL_REQUIRED` abgewiesen.

### Requirement: APF-R06 Hierarchische Ressourcen- und Kostenlimits
Das System SHALL Ressourcen atomar gegen Run-, Agenten-, Provider-, Zeitplan- und globale Limits reservieren. Nicht durchsetzbare oder überschrittene Grenzen MUST weitere kostenpflichtige Wirkungen sperren. Fehlende Usage bleibt unbekannt und behält die Reserve. Quant-Limits gelten unabhängig.

#### Scenario: Budgetprüfung und Abrechnung
GIVEN ein Modellversuch, Tool oder Experiment WHEN die Ressourcenprüfung entscheidet THEN begrenzt sie je nach Aktion Tokens, geschätzte und bestätigte Kosten, Modellaufrufe, Schritte, Toolaufrufe, aktive Laufzeit, Parallelität und Experimente. Verbrauchstokens sind ganzzahlig, Kosten Decimal mit ISO-Währung und Preisquellenversion. Providerverbindungstests verbrauchen ein eigenes Kontingent und die Provider- und Globalbudgets.

#### Scenario: APF-T-R11 Konkurrierende Reservierungen
GIVEN das globale Budget reicht für genau einen der konkurrierenden Requests WHEN beide atomar reservieren THEN erreicht nur ein Request den Gateway und der andere endet mit `BUDGET_EXHAUSTED`.

#### Scenario: APF-T-R12 Aggregierte Quant-Grenze
GIVEN ein Agent hat seine Experimentgrenze erreicht WHEN er weitere Runs oder Drafts erzeugt THEN wird kein zusätzlicher agentengebundener Start angenommen und die regulären Quant-Limits bleiben wirksam.

### Requirement: APF-R07 Einmalige Verarbeitungsfreigabe für externen Dialogkontext
Der Supervisor MUST eine frische, zweck- und inhaltsgebundene Betreiberfreigabe vor genau einem externen Interpretationsversuch atomar verbrauchen. Der Gateway MUST die aktuelle Zulassung und die unveränderte Projektion prüfen. Widerruf, abgelaufene oder verlorene Bindung MUST einen weiteren Versand sperren. Freigaben erteilen keine fachliche Bestätigung, Toolrechte oder Quant-Admission.

#### Scenario: APF-T-R14 Replay und konkurrierende Sendefreigaben
GIVEN eine serverseitig erzeugte Vorschau zu einer bestimmten Dialogversion WHEN der authentifizierte Betreiber zwei gleichzeitige Freigabeanfragen stellt THEN wird höchstens eine bindungsgleiche Freigabe angenommen und genau ein Versuch kann sie verbrauchen. Der identische Request liefert denselben Beleg. Andere Schlüssel für dieselbe Vorschau, Änderungen an Dialog, Zweck, Datenklassen, Policy, Provider, Modell, Credentialgeneration oder Kontext sowie eine veraltete Fence sperren Dispatch.

#### Scenario: APF-T-R15 Widerruf und Ergebnisannahme
GIVEN ein externes Interpretationsgesuch mit ausstehender Antwort WHEN der Betreiber widerruft THEN verhindert ein Widerruf vor Dispatchautorisierung den Versand. Nach Dispatchautorisierung darf die Anfrage bereits in flight sein. Das System stoppt weitere Verarbeitung soweit möglich und unterbindet nach dem Widerrufscommit jede neue Übernahme des Ergebnisses. Eine mögliche Providerwirkung und unbekannte Kosten bleiben sichtbar.

#### Scenario: APF-T-R16 Wiederaufnahme ohne Versandprojektion
GIVEN ein Restart hat den flüchtigen Freigabekontext verloren WHEN der Dialog oder der identische Freigaberequest erneut geöffnet wird THEN zeigt das System den dauerhaft gespeicherten, inhaltsfreien Status und bestätigte Hypothesenstände. Es rekonstruiert keinen versendbaren Prompt aus Historie oder Modelloutput. Ein neuer externer Versuch erfordert eine neue Vorschau und ausdrückliche Freigabe, ein lokaler Klärungsschritt bleibt möglich.

#### Scenario: APF-T-R17 Timeout nach verbrauchter Freigabe
GIVEN der Supervisor hat eine Sendefreigabe verbraucht WHEN Antwort oder Usage fehlen oder der Gateway den Nichtversand später bestätigt THEN führt der Lauf keinen automatischen Wiederholungsversand aus. Dauerhaft bestätigter Nichtversand erlaubt die Auflösung der Reserve. Unbekannter Versand behält die konservative Bindung. Bereits dauerhaft validierte Ergebnisse bleiben für normale Wiederaufnahme referenzierbar.
