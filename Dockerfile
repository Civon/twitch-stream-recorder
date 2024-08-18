FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Install system dependencies and Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir streamlink requests

# Copy the script and config files into the container
COPY twitch-recorder.py .streamlinkrc config.py /app/

# Set environment variables (can be overridden at runtime)
ENV username=
ENV client_id=
ENV client_secret=
ENV twitch_oauth_token=

# Run the script
ENTRYPOINT ["python", "twitch-recorder.py"]
