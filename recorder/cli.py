"""Command line entry points.

``record``  One recording session, then exit. This is what a Kubernetes Job
            runs: the controller decides when a channel is live, the Job
            records it once, and its exit code tells the Job whether to retry.
``watch``   The original long-running loop for one channel (Docker Compose,
            bare metal, the legacy per-channel Deployment).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

from recorder.events import EventEmitter, runtime_fields, sinks_from_spec
from recorder.session import RecordingSession, SessionConfig, finalize_leftovers
from recorder.twitch import HelixClient

EXIT_RETRY = 75  # EX_TEMPFAIL: the stream is still live, the Job should run again
MAX_EMPTY_PARTS = 5

log = logging.getLogger("recorder")


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    oauth_token: str = ""
    username: str = ""
    root_path: str = "/app/rec"
    event_sinks: str = "stdout"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            client_id=env.get("client_id", ""),
            client_secret=env.get("client_secret", ""),
            oauth_token=env.get("twitch_oauth_token", ""),
            username=env.get("username", ""),
            root_path=env.get("ROOT_PATH", "/app/rec"),
            event_sinks=env.get("EVENT_SINKS", "stdout"),
        )

    @classmethod
    def from_config_module(cls, config) -> "Settings":
        """Values from the legacy ``config.py`` (bare-metal installs edit it by hand)."""
        base = cls.from_env()
        return cls(
            client_id=getattr(config, "client_id", None) or base.client_id,
            client_secret=getattr(config, "client_secret", None) or base.client_secret,
            oauth_token=getattr(config, "twitch_oauth_token", None) or base.oauth_token,
            username=getattr(config, "username", None) or base.username,
            root_path=getattr(config, "root_path", None) or base.root_path,
            event_sinks=base.event_sinks,
        )


def _log_level(value: str) -> int:
    level = getattr(logging, value.upper(), None)
    if not isinstance(level, int):
        raise argparse.ArgumentTypeError(f"Invalid log level: {value}")
    return level


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-q", "--quality", default="best", help="streamlink quality (default: best)")
    common.add_argument("-l", "--log", "--logging", dest="log_level", type=_log_level, default=logging.INFO,
                        help="log level, e.g. debug, info, warn")
    common.add_argument("--disable-ffmpeg", action="store_true", help="move captures without remuxing")
    common.add_argument("--root-path", help="recordings root (default: $ROOT_PATH or /app/rec)")
    common.add_argument("--metadata-interval", type=float, default=30.0,
                        help="seconds between metadata polls while recording (default: 30)")
    common.add_argument("--event-sinks", help="comma list of stdout, log, none (default: $EVENT_SINKS or stdout)")

    parser = argparse.ArgumentParser(prog="recorder")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", parents=[common], help="record one live session and exit")
    rec.add_argument("--login", help="channel login (default: $username)")
    rec.add_argument("--stream-id", help="only record if this broadcast is still the live one")
    rec.add_argument("--container", choices=("mkv", "mp4"), default="mkv")

    watch = sub.add_parser("watch", parents=[common], help="poll one channel forever (legacy mode)")
    watch.add_argument("-u", "--username", help="channel login (default: $username)")
    watch.add_argument("--refresh", type=float, default=15.0, help="seconds between live checks (min 15)")
    watch.add_argument("--container", choices=("mkv", "mp4"), default="mp4")
    return parser


def _session_config(args, settings: Settings, login: str) -> SessionConfig:
    return SessionConfig(
        login=login,
        quality=args.quality,
        root_path=args.root_path or settings.root_path,
        container=args.container,
        disable_ffmpeg=args.disable_ffmpeg,
        metadata_interval=args.metadata_interval,
        oauth_token=settings.oauth_token,
        streamlink_config=os.path.join(os.getcwd(), ".streamlinkrc"),
    )


def record(args, settings: Settings, helix: HelixClient, emitter: EventEmitter,
           sleep=time.sleep) -> int:
    """Record one broadcast until it ends, in as many parts as it takes.

    streamlink can exit cleanly while the broadcast is still live (an HLS
    hiccup). Instead of ending the Job, which would leave the rest of the
    broadcast unrecorded, start the next part right away in the same pod.
    """
    login = (args.login or settings.username).lower()
    if not login:
        log.error("No channel login given (--login or $username)")
        return 2
    meta = helix.get_stream(login)
    if meta is None or (args.stream_id and meta.stream_id != args.stream_id):
        reason = "offline" if meta is None else "stream_mismatch"
        log.info("Not recording %s: %s", login, reason)
        emitter.emit("recording.skipped", **{"channel.login": login, "stream.id": args.stream_id, "reason": reason})
        return 0

    cfg = _session_config(args, settings, login)
    stopping = threading.Event()
    current: list[RecordingSession] = []

    def on_sigterm(*_):
        stopping.set()
        for s in current:
            s.request_stop()

    signal.signal(signal.SIGTERM, on_sigterm)
    finalize_failed = False
    empty_parts = 0
    part = 0
    while True:
        session = RecordingSession(cfg, helix, emitter, meta, part=part)
        current[:] = [session]
        result = session.run()
        finalize_failed |= result.outcome == "finalize_failed"
        empty_parts = empty_parts + 1 if result.outcome == "no_output" else 0
        if empty_parts and not stopping.is_set():
            # Helix can report a stream as live for a while after it ended.
            sleep(min(60, 15 * empty_parts))
        try:
            still = helix.get_stream(login)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not re-check %s after capture ended: %s", login, e)
            still = None
        if still is None or still.stream_id != meta.stream_id:
            break
        if result.stopped or stopping.is_set():
            log.warning("%s is still live but the pod is stopping; exiting %s so the Job retries", login, EXIT_RETRY)
            return EXIT_RETRY
        if empty_parts >= MAX_EMPTY_PARTS:
            log.warning("%s reported live but %s captures were empty; giving up", login, empty_parts)
            break
        part += 1
        meta = still
        log.info("%s is still live; recording part %s", login, part)
    return 1 if finalize_failed else 0


def watch(args, settings: Settings, helix: HelixClient, emitter: EventEmitter) -> int:
    login = (args.username or settings.username).lower()
    if not login:
        log.error("No channel login given (-u or $username)")
        return 2
    cfg = _session_config(args, settings, login)
    stop = threading.Event()
    session_holder: list[RecordingSession] = []

    def on_sigterm(*_):
        stop.set()
        for s in session_holder:
            s.request_stop()

    signal.signal(signal.SIGTERM, on_sigterm)

    try:
        finalize_leftovers(cfg, emitter)
    except Exception as e:  # noqa: BLE001
        log.error("Error processing recorded files: %s", e)

    refresh = args.refresh
    if refresh < 15:
        log.warning("Check interval should not be lower than 15 seconds")
        refresh = 15
    log.info("Checking for %s every %s seconds, recording with %s quality", login, refresh, args.quality)
    while not stop.is_set():
        try:
            meta = helix.get_stream(login)
        except Exception as e:  # noqa: BLE001
            log.error("Error checking %s, trying again in 5 minutes: %s", login, e)
            stop.wait(300)
            continue
        if meta is None:
            log.debug("%s currently offline, checking again in %s seconds", login, refresh)
            stop.wait(refresh)
            continue
        log.info("%s online, stream recording in session", login)
        session = RecordingSession(cfg, helix, emitter, meta)
        session_holder[:] = [session]
        session.run()
        session_holder.clear()
        log.info("Processing is done, going back to checking...")
        stop.wait(refresh)
    return 0


def configure_logging(level: int, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=level, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings or Settings.from_env()
    # watch keeps the legacy twitch-recorder.log file; a Job only logs to stderr.
    configure_logging(args.log_level, "twitch-recorder.log" if args.command == "watch" else None)
    if not settings.client_id or not settings.client_secret:
        log.error("client_id and client_secret are required")
        return 2

    emitter = EventEmitter(sinks_from_spec(args.event_sinks or settings.event_sinks), runtime_fields())
    helix = HelixClient(settings.client_id, settings.client_secret)
    if args.command == "record":
        return record(args, settings, helix, emitter)
    return watch(args, settings, helix, emitter)


if __name__ == "__main__":
    sys.exit(main())
