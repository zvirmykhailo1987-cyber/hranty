# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HRANTY_ENV=production \
    DATA_DIR=/data \
    PORT=8070 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

# Optional: LibreOffice renders DOCX/XLSX/PPTX previews as PDF. Off by default to keep the
# demo image small (demo documents are plain text). Enable with:
#   docker compose build --build-arg OFFICE_PREVIEW=1
ARG OFFICE_PREVIEW=0
RUN if [ "$OFFICE_PREVIEW" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends libreoffice-writer-nogui libreoffice-calc-nogui \
         libreoffice-impress-nogui fonts-dejavu-core fonts-liberation2 \
      && apt-get clean; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY templates ./templates
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Behind a reverse proxy set FORWARDED_ALLOW_IPS to the proxy's address so the login limiter
# sees real client IPs (uvicorn reads it with --proxy-headers).
# Unprivileged runtime user; the database and uploads live on the /data volume.
RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p /data && chown app:app /data \
 && chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER app
VOLUME ["/data"]
EXPOSE 8070

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8070/health', timeout=4)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8070", "--proxy-headers"]
