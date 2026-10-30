ARG PYTHON_BASE=agentic-trading-lab-dev-build-smoke@sha256:c40719cab30222c1dd7032d68efd6b7206834ccfd3cdc3c06034b607248f7f14
ARG POSTGRES_BASE=postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336
FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS clock-build
USER root
# A dated, signed Debian archive fixes the compiler dependency closure.
RUN printf '%s\n' 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261005T000000Z/ trixie main' > /etc/apt/sources.list \
    && rm /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc make libc6-dev
COPY libfaketime-86b37fde2fed7336ea2d0c17928e3015a55d9b4a.tar.gz /tmp/source.tar.gz
RUN echo 'd98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e  /tmp/source.tar.gz' | sha256sum -c - \
    && mkdir /tmp/clock-source /opt/test-clock \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /tmp/clock-source \
    && make -C /tmp/clock-source/src all FAKETIME_COMPILE_CFLAGS='-UFAKE_STAT -UFAKE_UTIME -UFAKE_SLEEP -Wno-unused-function' \
    && cp /tmp/clock-source/src/libfaketime.so.1 /tmp/clock-source/src/libfaketimeMT.so.1 /opt/test-clock/ \
    && cp /tmp/clock-source/README /tmp/clock-source/COPYING /opt/test-clock/ \
    && dpkg-query -W > /opt/test-clock/compiler-packages.txt \
    && sha256sum /opt/test-clock/*.so.1 > /opt/test-clock/libraries.sha256 \
    && printf '%s\n' '0.9.13' '86b37fde2fed7336ea2d0c17928e3015a55d9b4a' > /opt/test-clock/source-version.txt

FROM ${POSTGRES_BASE} AS postgres-clock
COPY --from=clock-build /opt/test-clock /opt/test-clock
LABEL org.agentic-trading-lab.test-only="paper-process-clock" \
      org.agentic-trading-lab.clock-source="86b37fde2fed7336ea2d0c17928e3015a55d9b4a"

FROM ${PYTHON_BASE} AS python-clock
COPY --from=clock-build /opt/test-clock /opt/test-clock
LABEL org.agentic-trading-lab.test-only="paper-process-clock" \
      org.agentic-trading-lab.clock-source="86b37fde2fed7336ea2d0c17928e3015a55d9b4a"
