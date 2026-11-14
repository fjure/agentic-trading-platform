ARG RESEARCH_TEST_BASE=agentic-trading-lab-dev-build-smoke:latest
FROM ${RESEARCH_TEST_BASE}
USER root
RUN /usr/local/bin/python -m pip install --no-cache-dir --no-deps \
    --target /opt/lab/.venv/lib/python3.13/site-packages \
    pytest==9.1.1 pyyaml==6.0.3 iniconfig==2.3.1 packaging==26.3 \
    pluggy==1.6.0 pygments==2.21.0
USER 10001:10001
