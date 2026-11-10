# Binary prepared and hash-verified by scripts/prepare_minio.py; no third-party image.
# Reuse the project's CA-enabled Python base; MinIO runs as its own non-root user.
FROM registry.hub.docker.com/library/python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
RUN --network=none python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip
RUN useradd --uid 10001 --create-home minio && mkdir /data && chown minio:minio /data
COPY --chmod=755 minio /usr/local/bin/minio
RUN echo '53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c  /usr/local/bin/minio' | sha256sum -c -
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
