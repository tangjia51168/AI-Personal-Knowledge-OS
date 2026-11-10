FROM registry.hub.docker.com/library/python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY pyproject.toml alembic.ini requirements.lock ./
COPY requirements ./requirements
RUN useradd --create-home --uid 10001 knowledge

FROM base AS project-wheel
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes --only-binary=:all: -r requirements/build.lock
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
RUN --network=none pip wheel --no-build-isolation --no-deps --wheel-dir /project-wheel .

FROM base AS runtime-deps
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock

FROM runtime-deps AS test-deps
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes --only-binary=:all: -r requirements/dev.lock

FROM test-deps AS test
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY tests ./tests
COPY scripts ./scripts
COPY --from=project-wheel /project-wheel /project-wheel
RUN --network=none pip install --no-index --no-deps /project-wheel/*.whl && pip check
USER knowledge
CMD ["python", "-m", "pytest", "-m", "integration", "-v"]

FROM runtime-deps AS runtime
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY --from=project-wheel /project-wheel /project-wheel
RUN --network=none pip install --no-index --no-deps /project-wheel/*.whl && pip check \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip /project-wheel
USER knowledge
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "64"]
