import datetime
import enum
import getopt
import json
import logging
import os
import subprocess
import sys
import shutil
import time

import requests

import config
import wide_events
from telemetry import init_telemetry
from wide_events import WideEventEmitter

# Emit a disk_space_warning wide event once free space drops below this share.
DISK_FREE_WARNING_RATIO = float(os.environ.get("DISK_FREE_WARNING_RATIO", "0.10"))


class TwitchResponseStatus(enum.Enum):
    ONLINE = 0
    OFFLINE = 1
    NOT_FOUND = 2
    UNAUTHORIZED = 3
    ERROR = 4


class TwitchRecorder:
    def __init__(self):
        # global configuration
        self.ffmpeg_path = "ffmpeg"
        self.disable_ffmpeg = False
        self.refresh = 15
        self.root_path = config.root_path

        # user configuration
        self.username = config.username
        self.quality = "best"

        # observability
        self.telemetry = init_telemetry()
        self.tracer = self.telemetry.tracer
        self.events = WideEventEmitter()
        self.reconnect_attempts = 0

        # twitch configuration
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self.token_url = "https://id.twitch.tv/oauth2/token?client_id=" + self.client_id + "&client_secret=" \
                         + self.client_secret + "&grant_type=client_credentials"
        self.url = "https://api.twitch.tv/helix/streams"
        self.access_token = self.fetch_access_token()

        # twitch Oauth token
        self.twitch_oauth_token = getattr(config, 'twitch_oauth_token', None)

    def fetch_access_token(self):
        with self.tracer.start_as_current_span("twitch.oauth.token") as span:
            span.set_attribute("http.request.method", "POST")
            span.set_attribute("server.address", "id.twitch.tv")
            token_response = requests.post(self.token_url, timeout=15)
            self.telemetry.api_requests.add(
                1, {"twitch.api.endpoint": "oauth2/token",
                    "http.response.status_code": token_response.status_code})
            self._check_rate_limit(token_response, "oauth2/token")
            token_response.raise_for_status()
            token = token_response.json()
            return token["access_token"]

    def run(self):
        # path to recorded stream
        recorded_path = os.path.join(self.root_path, "recorded", self.username)
        # path to finished video, errors removed
        processed_path = os.path.join(self.root_path, "processed", self.username)

        self.events.set_context(**{
            "twitch.streamer_name": self.username,
            "twitch.quality": self.quality,
            "recorder.root_path": self.root_path,
            "recorder.ffmpeg_enabled": not self.disable_ffmpeg,
        })

        # create directory for recordedPath and processedPath if not exist
        if os.path.isdir(recorded_path) is False:
            os.makedirs(recorded_path)
        if os.path.isdir(processed_path) is False:
            os.makedirs(processed_path)

        if self.refresh < 15:
            logging.warning("Check interval should not be lower than 15 seconds")
            self.refresh = 15
            logging.info("System set check interval to 15 seconds")

        try:
            video_list = [f for f in os.listdir(recorded_path) if os.path.isfile(os.path.join(recorded_path, f))]
            if video_list:
                logging.info("File found in recorded folder: Processing previously recorded files")
            for f in video_list:
                recorded_filename = os.path.join(recorded_path, f)
                processed_filename = os.path.join(processed_path, f)
                self.process_recorded_file(recorded_filename, processed_filename)
        except Exception as e:
            self._record_error("startup_processing_failed", e)
            logging.error("Error processing recorded files: %s", e)

        logging.info("Checking for %s every %s seconds, recording with %s quality",
                     self.username, self.refresh, self.quality)
        self.loop_check(recorded_path, processed_path)

    def process_recorded_file(self, recorded_filename, processed_filename):
        with self.tracer.start_as_current_span("recording.process") as span:
            span.set_attribute("file.path", recorded_filename)
            span.set_attribute("recorder.ffmpeg_enabled", not self.disable_ffmpeg)
            started = time.time()
            if self.disable_ffmpeg:
                logging.debug("Moving: %s", recorded_filename)
                shutil.move(recorded_filename, processed_filename)
            else:
                logging.debug("Fixing %s", recorded_filename)
                self.ffmpeg_copy_and_fix_errors(recorded_filename, processed_filename)
            self.telemetry.processing_duration.record(
                time.time() - started, {"recorder.ffmpeg_enabled": not self.disable_ffmpeg})

    def ffmpeg_copy_and_fix_errors(self, recorded_filename, processed_filename):
        try:
            subprocess.call(
                [self.ffmpeg_path, "-err_detect", "ignore_err", "-i", recorded_filename, "-c", "copy",
                 processed_filename])
            os.remove(recorded_filename)
        except Exception as e:
            self._record_error("ffmpeg_failed", e)
            logging.error("Error in ffmpeg processing: %s", e)

    def check_user(self):
        info = None
        status = TwitchResponseStatus.ERROR
        with self.tracer.start_as_current_span("twitch.api.get_streams") as span:
            span.set_attribute("http.request.method", "GET")
            span.set_attribute("server.address", "api.twitch.tv")
            span.set_attribute("twitch.streamer_name", self.username)
            try:
                headers = {"Client-ID": self.client_id, "Authorization": "Bearer " + self.access_token}
                r = requests.get(self.url + "?user_login=" + self.username, headers=headers, timeout=15)
                self.telemetry.api_requests.add(
                    1, {"twitch.api.endpoint": "helix/streams",
                        "http.response.status_code": r.status_code})
                self._check_rate_limit(r, "helix/streams")
                r.raise_for_status()
                info = r.json()
                if info is None or not info["data"]:
                    status = TwitchResponseStatus.OFFLINE
                else:
                    status = TwitchResponseStatus.ONLINE
            except requests.exceptions.RequestException as e:
                if e.response:
                    if e.response.status_code == 401:
                        status = TwitchResponseStatus.UNAUTHORIZED
                    elif e.response.status_code == 404:
                        status = TwitchResponseStatus.NOT_FOUND
                span.record_exception(e)
                self._record_error("twitch_api_error", e)
                logging.error("Error checking user status: %s", e)
            span.set_attribute("twitch.stream.status", status.name.lower())
        return status, info

    def loop_check(self, recorded_path, processed_path):
        pwd = os.getcwd()
        while True:
            status, info = self.check_user()
            if status == TwitchResponseStatus.NOT_FOUND:
                logging.error("Username not found, invalid username or typo")
                time.sleep(self.refresh)
            elif status == TwitchResponseStatus.ERROR:
                logging.error("Unexpected error: %s. Will try again in 5 minutes",
                              datetime.datetime.now().strftime("%Hh%Mm%Ss"))
                self._emit_reconnection_attempt("api_error", 300)
                time.sleep(300)
            elif status == TwitchResponseStatus.OFFLINE:
                logging.debug("%s currently offline, checking again in %s seconds", self.username, self.refresh)
                time.sleep(self.refresh)
            elif status == TwitchResponseStatus.UNAUTHORIZED:
                logging.warning("Unauthorized, will attempt to log back in immediately")
                self._emit_reconnection_attempt("unauthorized", 0)
                self.access_token = self.fetch_access_token()
            elif status == TwitchResponseStatus.ONLINE:
                logging.info("%s online, stream recording in session", self.username)
                self.reconnect_attempts = 0

                channels = info["data"]
                channel = next(iter(channels), None)
                filename = self.username + " - " + datetime.datetime.now() \
                    .strftime("%Y-%m-%d %Hh%Mm%Ss") + " - " + channel.get("title") + ".mp4"

                # Clean filename from unnecessary characters
                filename = "".join(x for x in filename if x.isalnum() or x in [" ", "-", "_", "."])

                recorded_filename = os.path.join(recorded_path, filename)
                processed_filename = os.path.join(processed_path, filename)

                self.record_stream(pwd, channel, recorded_filename, processed_filename)

                logging.info("Processing is done, going back to checking...")
                time.sleep(self.refresh)

    def record_stream(self, pwd, channel, recorded_filename, processed_filename):
        """Run streamlink for one live session, then post-process the result."""
        stream_id = channel.get("id")
        stream_context = {
            "twitch.streamer_name": self.username,
            "twitch.stream_id": stream_id,
            "twitch.user_id": channel.get("user_id"),
            "twitch.stream.title": channel.get("title"),
            "twitch.stream.game_name": channel.get("game_name"),
            "twitch.stream.language": channel.get("language"),
            "twitch.stream.viewer_count": channel.get("viewer_count"),
            "twitch.stream.started_at": channel.get("started_at"),
            "twitch.quality": self.quality,
            "file.path": recorded_filename,
        }
        events = self.events.with_context(**stream_context)
        metric_attributes = {"twitch.streamer_name": self.username, "twitch.quality": self.quality}
        uptime_key = stream_id or recorded_filename

        with self.tracer.start_as_current_span("twitch.stream.record") as span:
            for key, value in stream_context.items():
                if value is not None:
                    span.set_attribute(key, value)

            self.telemetry.active_recordings.add(1, metric_attributes)
            self.telemetry.track_stream_start(uptime_key, metric_attributes)
            self._check_disk_space(events)
            events.emit(wide_events.STREAM_START,
                        message="Recording started for %s" % self.username,
                        recorder__oauth_token_used=self.twitch_oauth_token is not None)

            started = time.time()
            exit_code = None
            try:
                streamlink_args = ["streamlink", "--twitch-disable-ads"]
                if self.twitch_oauth_token is not None:
                    logging.debug("Use Twitch OAuth token")
                    streamlink_args.extend(["--twitch-api-header",
                                            "Authorization=OAuth " + self.twitch_oauth_token])
                streamlink_args.extend(["--config", pwd + "/.streamlinkrc"])
                streamlink_args.extend(["twitch.tv/" + self.username, self.quality,
                                        "-o", recorded_filename])
                exit_code = subprocess.call(streamlink_args)
            finally:
                duration = time.time() - started
                self.telemetry.active_recordings.add(-1, metric_attributes)
                self.telemetry.track_stream_end(uptime_key)
                self.telemetry.recording_duration.record(duration, metric_attributes)
                span.set_attribute("recorder.duration_seconds", duration)

            events.emit(wide_events.STREAM_END,
                        message="Stream ended for %s" % self.username,
                        recorder__duration_seconds=round(duration, 3),
                        recorder__streamlink_exit_code=exit_code)

            logging.info("Recording stream is done, processing video file")
            if os.path.exists(recorded_filename):
                media = self._describe_media(recorded_filename, duration)
                span.set_attribute("file.size", media["file.size"])
                self.telemetry.bytes_written.add(media["file.size"], metric_attributes)
                self.process_recorded_file(recorded_filename, processed_filename)
                events.emit(wide_events.RECORDING_COMPLETE,
                            message="Recording complete for %s" % self.username,
                            recorder__duration_seconds=round(duration, 3),
                            recorder__streamlink_exit_code=exit_code,
                            recorder__processed_path=processed_filename,
                            **{k.replace(".", "__"): v for k, v in media.items()})
            else:
                self.telemetry.errors.add(
                    1, dict(metric_attributes, **{"error.type": "output_file_missing"}))
                events.emit(wide_events.RECORDING_FAILED, level=logging.WARNING,
                            message="No output file produced for %s" % self.username,
                            error__type="output_file_missing",
                            recorder__duration_seconds=round(duration, 3),
                            recorder__streamlink_exit_code=exit_code)
                logging.warning("File not found for fixing")

    def _describe_media(self, path, duration):
        """Collect size/codec/bitrate for a recording; ffprobe is best-effort."""
        size = os.path.getsize(path)
        media = {
            "file.size": size,
            "file.name": os.path.basename(path),
            "media.duration_seconds": round(duration, 3),
            "media.bitrate_bps": int(size * 8 / duration) if duration > 0 else 0,
        }
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
                capture_output=True, text=True, timeout=30, check=True)
            streams = json.loads(probe.stdout).get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
            media.update({
                "media.video_codec": video.get("codec_name"),
                "media.audio_codec": audio.get("codec_name"),
                "media.width": video.get("width"),
                "media.height": video.get("height"),
                "media.frame_rate": video.get("r_frame_rate"),
            })
        except Exception as e:  # noqa: BLE001 - probing is optional metadata
            logging.debug("ffprobe unavailable for %s: %s", path, e)
        return {k: v for k, v in media.items() if v is not None}

    def _check_disk_space(self, events):
        try:
            usage = shutil.disk_usage(self.root_path)
        except OSError as e:
            logging.debug("Could not read disk usage for %s: %s", self.root_path, e)
            return
        free_ratio = usage.free / usage.total if usage.total else 1.0
        if free_ratio >= DISK_FREE_WARNING_RATIO:
            return
        events.emit(wide_events.DISK_SPACE_WARNING, level=logging.WARNING,
                    message="Low disk space on %s" % self.root_path,
                    disk__path=self.root_path,
                    disk__total_bytes=usage.total,
                    disk__used_bytes=usage.used,
                    disk__free_bytes=usage.free,
                    disk__free_ratio=round(free_ratio, 4),
                    disk__free_threshold_ratio=DISK_FREE_WARNING_RATIO)

    def _check_rate_limit(self, response, endpoint):
        remaining = response.headers.get("Ratelimit-Remaining")
        if response.status_code != 429 and (remaining is None or int(remaining) > 0):
            return
        self.telemetry.errors.add(1, {"error.type": "rate_limited", "twitch.api.endpoint": endpoint})
        self.events.emit(wide_events.API_RATE_LIMIT, level=logging.WARNING,
                         message="Twitch API rate limit reached on %s" % endpoint,
                         twitch__api__endpoint=endpoint,
                         http__response__status_code=response.status_code,
                         http__ratelimit__limit=response.headers.get("Ratelimit-Limit"),
                         http__ratelimit__remaining=remaining,
                         http__ratelimit__reset=response.headers.get("Ratelimit-Reset"),
                         http__retry_after=response.headers.get("Retry-After"))

    def _emit_reconnection_attempt(self, reason, backoff_seconds):
        self.reconnect_attempts += 1
        self.events.emit(wide_events.RECONNECTION_ATTEMPT, level=logging.WARNING,
                         message="Reconnecting to Twitch after %s" % reason,
                         reconnect__reason=reason,
                         reconnect__attempt=self.reconnect_attempts,
                         reconnect__backoff_seconds=backoff_seconds,
                         recorder__check_interval_seconds=self.refresh)

    def _record_error(self, error_type, exception):
        self.telemetry.errors.add(1, {
            "error.type": error_type,
            "exception.type": type(exception).__name__,
            "twitch.streamer_name": self.username,
        })


def main(argv):
    usage_message = "twitch-recorder.py -u <username> -q <quality>"

    try:
        opts, args = getopt.getopt(argv, "hu:q:l:", ["username=", "quality=", "logging=", "disable-ffmpeg"])
    except getopt.GetoptError:
        print(usage_message)
        sys.exit(2)

    for opt, arg in opts:
        if opt == "-h":
            print(usage_message)
            sys.exit()

    # Configure logging before the recorder boots, so that the OTel logging
    # handler installed during telemetry setup is not the root logger's first
    # handler (basicConfig is a no-op once any handler is attached).
    logging.basicConfig(filename="twitch-recorder.log", level=logging.INFO)
    logging.getLogger().addHandler(logging.StreamHandler())

    twitch_recorder = TwitchRecorder()

    for opt, arg in opts:
        if opt in ("-u", "--username"):
            twitch_recorder.username = arg
        elif opt in ("-q", "--quality"):
            twitch_recorder.quality = arg
        elif opt in ("-l", "--log", "--logging"):
            logging_level = getattr(logging, arg.upper(), None)
            if not isinstance(logging_level, int):
                raise ValueError("Invalid log level: %s" % logging_level)
            logging.info("Logging configured to %s", arg.upper())
            logging.getLogger().setLevel(logging_level)
        elif opt == "--disable-ffmpeg":
            twitch_recorder.disable_ffmpeg = True
            logging.info("FFmpeg disabled")

    twitch_recorder.run()


if __name__ == "__main__":
    main(sys.argv[1:])
