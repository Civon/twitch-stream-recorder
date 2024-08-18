FROM python:3.10-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache \
        gcc \
        musl-dev \
        libffi-dev

RUN pip install --no-cache-dir streamlink requests

# Stage 2: Final runtime stage
FROM python:3.10-alpine

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /usr/local /usr/local

COPY twitch-recorder.py .streamlinkrc config.py /app/

RUN apk add --no-cache ffmpeg

# Set environment variables
ENV username=
ENV client_id=
ENV client_secret=
ENV twitch_oauth_token=

ENTRYPOINT ["python", "twitch-recorder.py"]
