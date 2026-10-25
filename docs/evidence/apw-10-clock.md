# APW-10: Kompatibilität der Prozessuhr

Dieser Nachweis betrifft ausschließlich das Testwerkzeug. Die vollständige
Paperkette und ein echter Provider sind damit noch nicht zertifiziert. Alle
Prüfungen liefen mit eigener temporärer PostgreSQL-Instanz im internen Netz und
synthetischem lokalem HTTP, ohne echte Schlüssel oder normale Datenbankmounts.

- Quelle: libfaketime 0.9.13, Commit
  `86b37fde2fed7336ea2d0c17928e3015a55d9b4a`.
- SHA256 des offiziellen Commitarchivs:
  `d98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e`.
- Compilerstand: signierter Debian-Snapshot `20261005T000000Z`, GCC14 `14.2.0-19`,
  make `4.4.1-2`, libc-Entwicklungspaket `2.41-12+deb13u4`.
- Laufzeitbasis: vorhandenes Python3.13.16-Smokeimage und PostgreSQL18.6 Trixie;
  genaue Digests in `tests/integration/paper_clock/runtime.py`.
- Python-Uhrimage:
  `sha256:dcbaadfd8868e6a760868ee15da5a138483ef768aa3623e2c7dfca8fe93032ea`.
- PostgreSQL-Uhrimage:
  `sha256:98bc30efae325fe37f8bd4a0d8a465dd84a4a0dbe16427f9babfc2e1b21411c5`.
- SHA256 der normalen Bibliothek:
  `e5138d1791a687608bfc9611dbc5327e2c85f85c7cc4bf526038f422baada25b`.
- SHA256 der MT-Bibliothek:
  `04748d385bd935797e72ff792231f858cf78a3e8081ba2942ba3a8f32872a549`.

Der erste Build mit optionalen Sleep-Hooks bestand die PostgreSQL-Prüfung für
Statement-Timeouts nicht und wurde verworfen. Deaktivierte Dateisystem- und
Sleep-Hooks erhalten die nativen Kernelwartefristen. Upstreamquellen,
Produktionscode und SQL-Gates blieben unverändert.

Tatsächlicher Befehl:
`python -m pytest tests/integration/test_paper_test_clock.py -q`.
Ergebnis: **2 bestanden in 7,29s**, einschließlich 16 benannter Pflichtprüfungen
und der Ablehnung eingefrorener, skalierter oder externer Uhrdirektiven.
Virtuelle UTC: Montag `2026-10-26T14:30:10Z`; die Hostzeit blieb unverändert.
Die ignorierte `.tooling/output/paper-clock/compatibility.json` enthält die
einzelnen echten Laufzeiten und Artefakthashes. Native Event-/HTTP-/asyncio-/SQL-
und Lockfristen wurden gegen monotone Laufzeit geprüft; SQL-Timeoutproben:200ms.

Nach Änderungen an Image, Werkzeugkette oder Plattform erneut prüfen. Dieser
Nachweis allein belegt weder die Browser→API→Execution→Provider-Kette noch
Handelssicherheit. Der Browserharness initialisiert PostgreSQL ausdrücklich mit
`--encoding=UTF8 --locale=C.UTF-8`: Das reine `LC_ALL=C` der Uhrumgebung darf
keine neue SQL_ASCII-Datenbank für SQLAlchemy erzeugen.
