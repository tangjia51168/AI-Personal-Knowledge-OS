FROM registry.hub.docker.com/library/python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false EMBEDDING_BIND=0.0.0.0
WORKDIR /service
COPY infrastructure/embedding/requirements.lock ./requirements.lock
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes --only-binary=:all: -r requirements.lock \
    && useradd --uid 10001 --no-create-home embedding \
    && pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip
COPY infrastructure/embedding/server.py infrastructure/embedding/model.json ./
USER embedding
EXPOSE 8080
CMD ["python", "server.py"]
