# Intelligent Hospital Workforce Scheduling System
#
# Build:  docker compose build
# Run:    docker compose up -d
#
# Python 3.12 rather than the newest release: every dependency ships a
# prebuilt manylinux wheel for it, so the image builds without a compiler.

FROM python:3.12-slim

# Ghana is on GMT year-round and the portal stores naive local datetimes, so
# the container clock must match the hospital's. Without tzdata and TZ, the
# container runs on UTC under a different name and every rostered time,
# attendance stamp and audit entry is recorded against the wrong "today".
ENV TZ=Africa/Accra
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Dependencies first, so a code change does not invalidate the install layer.
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY . .

# Run as a non-root user. The instance directory is created and chowned here so
# that a named volume mounted over it inherits the right ownership.
RUN useradd --create-home --shell /bin/bash portal \
    && mkdir -p /app/instance \
    && chown -R portal:portal /app

COPY --chown=portal:portal docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER portal

EXPOSE 8000

# Uses the application's own health endpoint, which also probes the database.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "wsgi.py"]
