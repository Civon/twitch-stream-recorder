# Dockerfile for Ancalentari Twitch Stream Recorder

# Use an official Python image as the base
FROM python:3.10

# Set the working directory
WORKDIR /app

# Install required packages
RUN pip install streamlink requests ffmpeg

# Copy the script and config file into the container
COPY twitch-recorder.py .streamlinkrc config.py /app/

ENV username=
ENV client_id=
ENV client_secret=
ENV twitch_oauth_token=

# Run the script
ENTRYPOINT ["python", "twitch-recorder.py"]
