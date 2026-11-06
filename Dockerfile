# Garmin training dashboard: small web app, standard-library Python only.
# Builds for amd64 and arm64 (Intel/AMD PCs, Apple Silicon, Raspberry Pi 4/5).
FROM python:3.12-slim

# tzdata so TZ=Asia/Kolkata (etc.) gives the right "today"; tzdata-legacy adds
# the older names some browsers still report (Asia/Calcutta, US/Eastern, ...)
RUN apt-get update \
 && (apt-get install -y --no-install-recommends tzdata tzdata-legacy \
     || apt-get install -y --no-install-recommends tzdata) \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --chown=app:app src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GARMIN_DATA_DIR=/data \
    DASHBOARD_OUT=/data/dashboard.html \
    PORT=8080 \
    REFRESH_MINUTES=60 \
    TZ=UTC

USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"

CMD ["python", "src/server.py"]
