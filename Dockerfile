# Aeropt agent (tutor) — FastAPI, one process. Retrieval reads the Supabase retrieval
# Postgres (RETRIEVAL_DATABASE_URL in the env file); there is no index in the image.
#   docker build -t aeropt-agent .
#   docker run --rm -p 7860:7860 --env-file .env.prod aeropt-agent
#
# The build context must contain the query embedding model (gitignored; snapshotted by
# `make push`), so a query embeds offline in the same space as the corpus:
#   .local-run/models/potion-base-8M/
# deploy/push_space.sh stages exactly that for Hugging Face.

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim AS runtime

ARG TECTONIC_VERSION=0.17.0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=7860 \
    AEROPT_CARRY_DIR=/tmp/aeropt/carry \
    AEROPT_DIAGRAM_DIR=/tmp/aeropt/diagrams \
    HF_HUB_OFFLINE=1

# Diagrams: tectonic (static LaTeX engine, fetches packages on first use) and
# pdftocairo. Without them draw_diagram reports "not installed" and the tutor
# explains in words instead.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl poppler-utils fontconfig \
 && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin tectonic \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run the container as uid 1000.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin user

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY --chown=user:user . .
RUN test -f .local-run/models/potion-base-8M/model.safetensors \
 || (echo "Build context has no embedding model; run make push (or deploy/push_space.sh)." && exit 1)

USER user
# Fetch tectonic's LaTeX packages now: on first use they download for over a
# minute, which would time out the first diagram after every restart.
RUN python deploy/warm_tectonic.py
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/health',timeout=4).read()"]

# One worker on purpose: the rate limiter and the local caches are per process.
CMD ["sh", "-c", "exec uvicorn api_server:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 75"]
