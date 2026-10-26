FROM python:3.11-slim

ARG VERSION=1.0.0
ARG REVISION=unknown
ARG CREATED=""

LABEL maintainer="erwinvol" \
      org.opencontainers.image.title="Sole2Garmin" \
      org.opencontainers.image.description="Automatic sync service between Sole Fitness (via Strava bridge or Dyaco) and Garmin Connect with FIT device spoofing" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.source="https://github.com/erwinvol/Sole2Garmin" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    GARMIN_TOKEN_DIR=/data/garmin_tokens \
    STRAVA_TOKEN_DIR=/data/strava_tokens \
    SOLE_TOKEN_DIR=/data/sole_tokens \
    SYNC_INTERVAL_MINUTES=60

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create data directories
RUN mkdir -p /data/garmin_tokens /data/strava_tokens /data/sole_tokens /data/sample_activities

# Copy application source
COPY src/ /app/src/

VOLUME ["/data"]

EXPOSE 8080

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--daemon"]
