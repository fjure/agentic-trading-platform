FROM agentic-trading-lab-dev-build-smoke:latest
USER root
RUN /usr/local/bin/python -m pip install --no-cache-dir \
    --target /opt/lab/.venv/lib/python3.13/site-packages \
    jsonschema==4.26.0 jsonschema-specifications==2025.9.1 \
    referencing==0.37.0 rpds-py==2026.9.1 attrs==26.1.0 \
    pytest==9.1.1 pyyaml==6.0.3
USER 10001:10001
