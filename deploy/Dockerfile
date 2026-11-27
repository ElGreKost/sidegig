# A hosted sidegig board. Build from the repository root:
#
#     docker build -f deploy/Dockerfile -t sidegig:remote .
#
# The server listens on 8765 inside the container. Publish it on 127.0.0.1 only and put a TLS proxy
# in front (see deploy/README.md and deploy/example/). /data/state holds everything: teams.json, the
# token hashes, each team's board, groups and event log, and the audit logs; keep it on a volume.
# /data/board is only for servers set up before teams (their board stays there after migrate-teams).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SIDEGIG_STATE=/data/state

COPY pyproject.toml README.md LICENSE /tmp/sidegig/
COPY src /tmp/sidegig/src
RUN pip install /tmp/sidegig \
 && rm -rf /tmp/sidegig \
 && useradd --system --uid 10001 --user-group --home-dir /data --no-create-home --shell /usr/sbin/nologin sidegig \
 && mkdir -p /data/board /data/state \
 && chown -R sidegig:sidegig /data \
 && chmod 700 /data/state

USER sidegig
WORKDIR /data
VOLUME ["/data/board", "/data/state"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"]

ENTRYPOINT ["sidegig"]
CMD ["serve", "--state", "/data/state", "--host", "0.0.0.0", "--port", "8765"]
