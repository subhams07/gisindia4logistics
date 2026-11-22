# Evaluation Dockerfile for the GIS4Logistics API server.
# Not hardened for production. The data directory is not baked into the image:
# mount it at /app/data (docker-compose.yml does this read-only).
FROM python:3.11-slim

# Install system dependencies for GDAL/GEOS
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Run the service as an unprivileged user.
RUN addgroup --system app && adduser --system --ingroup app app

# Install Python requirements
# Install Python requirements (runtime only: notebook and test tools are skipped)
COPY requirements.txt .
RUN grep -vE '^(jupyter|pytest)' requirements.txt > /tmp/requirements-runtime.txt \
    && pip install --no-cache-dir -r /tmp/requirements-runtime.txt

# Copy project code (data/ is excluded by .dockerignore and mounted at run time)
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown app:app /app/data
RUN mkdir -p /app/outputs && chown -R app:app /app/outputs

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/health/ready || exit 1

# Start FastAPI server (host and port from GIS4LOGISTICS_HOST / GIS4LOGISTICS_PORT;
# `serve` refuses to start without the mounted data)
USER app
CMD ["python", "-m", "gisindia4logistics.cli", "serve"]
