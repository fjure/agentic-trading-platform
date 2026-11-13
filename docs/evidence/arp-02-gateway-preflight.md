# ARP-02 · Gatewaytransport und Egress

Arbeitsstand auf Basis von `d3dc34c6e527713e09109362939706fddd6cf87f`. GitHub bestätigt #96 als abgeschlossene Voraussetzung von #97.

Die Gateway-Komposition verbindet signierte Admission und Dispatch, das persistente Einmaljournal, gebundene Credentials und den festen CONNECT-Egress. Das optionale Compose-Profil trennt das interne Gatewaynetz vom Egress-Uplink. Egress startet gesperrt. TLS verwendet einen festen Trust Store und eine vom Prozessumfeld unabhängige Konfiguration.

`pytest tests/unit/test_llm_tls_socket.py tests/unit/test_egress.py tests/unit/test_llm_gateway_app.py tests/unit/test_llm_provider_http.py tests/unit/test_llm_offline_gateway.py tests/unit/test_llm_provider_wire.py -q` ergab 63 bestandene Tests. Die TLS-Fixtures verwenden beide echten Wire-Formen, synthetische Zertifikate und ein explizites Loopback-Routing. Sie belegen signierten Einmalversand, Replay-Sperre, 429, Redirect-Sperre, Modellabweichung und Ablehnung einer unbekannten CA. Produktionsegress und DNS-Allowlist werden separat geprüft. Es gab keinen externen Providerrequest.

Der unabhängige Review führte zu einer Gesamtlaufzeitgrenze für das Provider-I/O, einem expliziten TLS-Kontext und konservativer Behandlung unbekannter Abrechnungskomponenten. Der Einstieg nimmt die feste Provider-Komposition nur mit vollständigen Ports an. Compose startet weiterhin gesperrt. ARP-03 liefert den dauerhaften Credential-Reader und ARP-04 verbindet die produktiven Profil- und Admission-Ports mit dem Diensteinstieg.

`pytest tests/integration/test_agent_research_runtime.py -q` bestand auf temporärem PostgreSQL und prüfte die bestehende transaktionale Admission, Reservierung, Fencing und Wiederaufnahme. Die TCP/TLS-Prüfung belegt zusätzlich, dass eine signierte Dispatch-Anfrage bei verweigerter Admission keinen Providerrequest erzeugt. Der dedizierte Egress wurde über seine Compose-Konfiguration und separat über die Resolver-Tests geprüft.
