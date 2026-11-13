# ADR 01 · Separater Gateway-Egress

Status: vorgeschlagen.

Der produktive Einstiegspunkt erhält explizite Abhängigkeiten für signierten Dispatch, Credential-Read, das persistente Attempt-Journal und einen eigenen eingeschränkten Egress-Dienst. Der Gateway besitzt nur interne Netze. Ein zweckgebundener CONNECT-Kanal transportiert Ende-zu-Ende-TLS zwischen Gateway und Provider, ohne Schlüssel oder Klartext im Egress-Dienst. Der Offline-Einstieg und seine Doubles bleiben als unabhängige Testkomposition verfügbar.

Ein direkt extern vernetzter Gateway hätte eine größere Ausbruchsfläche. Die bestehenden Broker-/Jev-Egress-Dienste haben andere Rollen und Ziele. Die separate Komposition erlaubt einen Netzwerkbeweis mit echtem TCP/TLS gegen einen isolierten Emulator und eine separat autorisierte Bestätigung gegen das reale Ziel.

Die Probe reserviert über dieselbe Ledgergrenze wie andere Modellversuche. [ProviderSmokeBudgetPolicyV1](../smoke-budget.md) bindet Nachweis und Höchstbetrag. Ein kleiner synthetischer Payload begründet nur mit belegter abrechenbarer Inputschranke eine kleinere Reserve.
