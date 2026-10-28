# WiZ Control, headless server mode: the web UI on port 8765.
# Run it with host networking (see docker-compose.yml) so it can reach the bulbs
# over UDP and discover them by broadcast.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WIZCONTROL_CONFIG_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wizcontrol ./wizcontrol

RUN useradd --system --uid 1000 --home /app wiz && mkdir -p /data
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/meta' % os.environ.get('WIZ_PORT', '8765'), timeout=4)"

# Starts as root only to hand /data (often created root-owned by Docker) to the
# app user, then runs the server as that unprivileged user.
CMD ["sh", "-c", "chown -R wiz /data && exec setpriv --reuid=wiz --regid=wiz --init-groups python -m wizcontrol --server --host 0.0.0.0 --port ${WIZ_PORT:-8765}"]
