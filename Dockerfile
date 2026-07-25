FROM python:3.10-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

# Debian rather than Alpine, because grpcio/protobuf/psutil - pulled in by the
# OTLP exporters and runtime metrics - publish no musl wheels.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.10-slim

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

COPY twitch-recorder.py telemetry.py wide_events.py .streamlinkrc config.py /app/

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV username=
ENV client_id=
ENV client_secret=
ENV twitch_oauth_token=

# OpenTelemetry stays inert until an OTLP endpoint is configured.
ENV OTEL_SERVICE_NAME=twitch-stream-recorder
ENV OTEL_EXPORTER_OTLP_ENDPOINT=
ENV OTEL_EXPORTER_OTLP_PROTOCOL=grpc
ENV OTEL_RESOURCE_ATTRIBUTES=
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "twitch-recorder.py"]
