FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
WORKDIR /opt/lab
RUN python -m pip install --no-cache-dir uv==0.13.0
COPY pyproject.toml uv.lock ./
COPY services ./services
COPY packages ./packages
RUN uv sync --frozen --no-dev --no-editable --no-python-downloads --python /usr/local/bin/python

FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ENV PATH="/opt/lab/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /opt/lab/.venv /opt/lab/.venv
COPY services /opt/lab/services
COPY packages /opt/lab/packages
COPY config /opt/lab/config
COPY contracts/schemas/v1/backtesting.schema.json /opt/lab/contracts/schemas/v1/backtesting.schema.json
COPY alembic.ini /opt/lab/alembic.ini
ARG SOURCE_REVISION=""
RUN printf '%s' "$SOURCE_REVISION" > /opt/lab/git-revision
COPY pyproject.toml uv.lock /opt/lab/
RUN python -m services.runtime.backtesting stamp
USER 10001:10001
WORKDIR /opt/lab
CMD ["python", "-m", "services.bootstrap"]
