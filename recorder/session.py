"""One recording session: capture a live stream, track its metadata, finalize.

While streamlink writes the stream to ``recorded/<login>/``, the session polls
Helix every ``metadata_interval`` seconds. Each change of category, title,
tags, language, or mature flag becomes a ``stream.metadata_changed`` event
carrying ``recording.offset_ms``, the position in the file where it happened.
When streamlink exits, the file is remuxed into ``processed/<login>/`` with
chapters, and one ``recording.session`` event summarizes the whole session.

The raw capture is deleted only after the remux succeeded and produced a
non-empty file; on failure it stays in ``recorded/`` for a later retry.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from recorder import chapters as chapters_mod
from recorder.events import EventEmitter, JsonlFileSink
from recorder.metadata import StreamMetadata, diff
from recorder.twitch import HelixClient

VIDEO_EXTENSIONS = (".mp4", ".ts", ".mkv")
EVENTS_SUFFIX = ".events.jsonl"
CHAPTERS_SUFFIX = ".chapters.json"
CONTAINERS = ("mp4", "mkv")

log = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    login: str
    quality: str = "best"
    root_path: str = "/app/rec"
    container: str = "mp4"
    disable_ffmpeg: bool = False
    metadata_interval: float = 30.0
    oauth_token: str | None = None
    streamlink_config: str | None = None
    streamlink_path: str = "streamlink"
    ffmpeg_path: str = "ffmpeg"

    def __post_init__(self):
        if self.container not in CONTAINERS:
            raise ValueError(f"container must be one of {CONTAINERS}, got {self.container!r}")
        # An empty token would send "Authorization=OAuth " to Twitch; treat it as unset.
        self.oauth_token = self.oauth_token or None

    @property
    def recorded_dir(self) -> str:
        return os.path.join(self.root_path, "recorded", self.login)

    @property
    def processed_dir(self) -> str:
        return os.path.join(self.root_path, "processed", self.login)


@dataclass
class FinalizeResult:
    ok: bool
    output_path: str | None
    returncode: int | None = None


@dataclass
class SessionResult:
    outcome: str
    raw_path: str
    output_path: str | None
    streamlink_returncode: int | None
    duration_ms: int
    metadata_changes: int
    stopped: bool
    timeline: list[chapters_mod.TimelinePoint] = field(default_factory=list)


def safe_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_.")


def _parse_rfc3339(value: str) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def finalize(raw_path: str, output_path: str, *, disable_ffmpeg: bool = False, ffmpeg_path: str = "ffmpeg",
             ffmeta_path: str | None = None, run: Callable[..., Any] = subprocess.run) -> FinalizeResult:
    """Remux (or move) a raw capture; delete the raw file only on success."""
    if disable_ffmpeg:
        shutil.move(raw_path, output_path)
        return FinalizeResult(True, output_path)

    cmd = [ffmpeg_path, "-nostdin", "-y", "-err_detect", "ignore_err", "-i", raw_path]
    if ffmeta_path:
        # Map only audio/video: Twitch TS carries an ID3 data stream that MKV cannot hold.
        cmd += ["-i", ffmeta_path, "-map", "0:v?", "-map", "0:a?", "-map_chapters", "1"]
    cmd += ["-c", "copy", output_path]
    try:
        returncode = run(cmd).returncode
    except OSError as e:
        log.error("Could not run ffmpeg: %s", e)
        return FinalizeResult(False, None)
    if returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        os.remove(raw_path)
        return FinalizeResult(True, output_path, returncode)
    log.error("ffmpeg exited %s for %s; keeping the raw file", returncode, raw_path)
    return FinalizeResult(False, None, returncode)


def _write_chapters(base_out: str, stream_id: str, recorded_at: str,
                    chapters: list[dict[str, Any]]) -> str:
    path = base_out + CHAPTERS_SUFFIX
    with open(path, "w", encoding="utf-8") as f:
        json.dump(chapters_mod.chapters_document(stream_id, recorded_at, chapters), f, ensure_ascii=False, indent=2)
    return path


def finalize_with_sidecars(cfg: SessionConfig, raw_path: str, timeline: list[chapters_mod.TimelinePoint],
                           duration_ms: int, stream_id: str, recorded_at: str,
                           run: Callable[..., Any] = subprocess.run) -> FinalizeResult:
    """Remux one raw file into processed/, embedding and writing chapters."""
    os.makedirs(cfg.processed_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(raw_path))[0]
    base_raw = os.path.join(cfg.recorded_dir, base_name)
    base_out = os.path.join(cfg.processed_dir, base_name)
    if cfg.container == "mkv" and not cfg.disable_ffmpeg:
        output_path = base_out + ".mkv"
    else:
        output_path = base_out + os.path.splitext(raw_path)[1]

    chapter_list = chapters_mod.build_chapters(timeline, duration_ms) if timeline else []
    ffmeta_path = None
    if chapter_list and cfg.container == "mkv" and not cfg.disable_ffmpeg:
        ffmeta_path = base_raw + ".ffmeta"
        with open(ffmeta_path, "w", encoding="utf-8") as f:
            f.write(chapters_mod.to_ffmetadata(chapter_list))

    result = finalize(raw_path, output_path, disable_ffmpeg=cfg.disable_ffmpeg, ffmpeg_path=cfg.ffmpeg_path,
                      ffmeta_path=ffmeta_path, run=run)
    if ffmeta_path and os.path.exists(ffmeta_path):
        os.remove(ffmeta_path)
    if result.ok and chapter_list:
        _write_chapters(base_out, stream_id, recorded_at, chapter_list)
    return result


def _move_events_file(cfg: SessionConfig, raw_path: str) -> None:
    base_name = os.path.splitext(os.path.basename(raw_path))[0]
    src = os.path.join(cfg.recorded_dir, base_name + EVENTS_SUFFIX)
    if os.path.exists(src):
        shutil.move(src, os.path.join(cfg.processed_dir, base_name + EVENTS_SUFFIX))


class RecordingSession:
    def __init__(self, cfg: SessionConfig, helix: HelixClient, emitter: EventEmitter, initial: StreamMetadata,
                 *, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 popen: Callable[..., Any] = subprocess.Popen, run: Callable[..., Any] = subprocess.run,
                 part: int = 0):
        self.cfg = cfg
        self.part = part
        self.helix = helix
        self.emitter = emitter
        self.initial = initial
        self._clock = clock
        self._wall_clock = wall_clock
        self._popen = popen
        self._run = run
        self._proc = None
        self._stop_requested = False
        self.metadata_changes = 0
        self.title_changes = 0
        self.category_changes = 0
        self.poll_errors = 0

    def request_stop(self) -> None:
        """Stop capturing (e.g. on SIGTERM); the session still finalizes what it has."""
        self._stop_requested = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()

    def streamlink_args(self, output_path: str) -> list[str]:
        args = [self.cfg.streamlink_path, "--twitch-disable-ads"]
        if self.cfg.oauth_token:
            args += ["--twitch-api-header", "Authorization=OAuth " + self.cfg.oauth_token]
        if self.cfg.streamlink_config and os.path.exists(self.cfg.streamlink_config):
            args += ["--config", self.cfg.streamlink_config]
        args += ["twitch.tv/" + self.cfg.login, self.cfg.quality, "-o", output_path]
        return args

    def _stream_offset_ms(self, meta: StreamMetadata) -> int | None:
        started = _parse_rfc3339(meta.started_at)
        if started is None:
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        return max(0, int((now - started).total_seconds() * 1000))

    def _point(self, offset_ms: int, meta: StreamMetadata) -> chapters_mod.TimelinePoint:
        return chapters_mod.TimelinePoint(offset_ms, meta.category_id, meta.category_name, meta.title, meta.tags)

    def _poll_metadata(self, em: EventEmitter, current: StreamMetadata, t0: float,
                       timeline: list[chapters_mod.TimelinePoint]) -> StreamMetadata:
        try:
            latest = self.helix.get_stream(self.cfg.login)
        except Exception as e:  # noqa: BLE001 - metadata is best effort, the recording is not
            self.poll_errors += 1
            log.warning("Metadata poll for %s failed: %s", self.cfg.login, e)
            return current
        if latest is None:
            return current
        offset_ms = int((self._clock() - t0) * 1000)
        if latest.stream_id != current.stream_id:
            em.emit("stream.restarted", **latest.event_fields(), **{
                "stream.id.before": current.stream_id, "recording.offset_ms": offset_ms})
            timeline.append(self._point(offset_ms, latest))
            return latest
        change = diff(current, latest)
        if change is None:
            return latest
        self.metadata_changes += 1
        if "category" in change.fields:
            self.category_changes += 1
        if "title" in change.fields:
            self.title_changes += 1
        log.info("%s metadata changed at %ss: %s", self.cfg.login, offset_ms // 1000, change.summary())
        em.emit("stream.metadata_changed", **change.event_fields(), **{
            "recording.offset_ms": offset_ms,
            "stream.offset_ms": self._stream_offset_ms(latest),
        })
        timeline.append(self._point(offset_ms, latest))
        return latest

    def run(self) -> SessionResult:
        cfg = self.cfg
        os.makedirs(cfg.recorded_dir, exist_ok=True)
        os.makedirs(cfg.processed_dir, exist_ok=True)
        started_wall = self._wall_clock()
        recorded_at = started_wall.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        base_name = f"{cfg.login} - {started_wall:%Y-%m-%d %Hh%Mm%Ss} - {self.initial.title}"
        if self.part:
            # A later part of the same broadcast can start within the same second.
            base_name += f" - part{self.part}"
        base_name = safe_filename(base_name)
        # Legacy MP4 mode keeps its historical names; MKV mode captures to .ts first.
        raw_ext = ".ts" if cfg.container == "mkv" else ".mp4"
        raw_path = os.path.join(cfg.recorded_dir, base_name + raw_ext)
        events_path = os.path.join(cfg.recorded_dir, base_name + EVENTS_SUFFIX)

        em = self.emitter.bind(JsonlFileSink(events_path), **{
            "recording.id": f"{cfg.login}-{self.initial.stream_id}-{started_wall:%Y%m%dT%H%M%S}",
            "recording.quality": cfg.quality,
            "recording.container": cfg.container,
            "recording.part": self.part,
        })
        timeline = [self._point(0, self.initial)]
        em.emit("recording.started", **self.initial.event_fields(), **{
            "recording.offset_ms": 0,
            "stream.offset_ms": self._stream_offset_ms(self.initial),
            "recording.file": raw_path,
        })

        t0 = self._clock()
        current = self.initial
        self._proc = self._popen(self.streamlink_args(raw_path))
        while True:
            try:
                returncode = self._proc.wait(timeout=cfg.metadata_interval)
                break
            except subprocess.TimeoutExpired:
                pass
            if not self._stop_requested:
                current = self._poll_metadata(em, current, t0, timeline)
        duration_ms = int((self._clock() - t0) * 1000)

        if not os.path.isfile(raw_path) or os.path.getsize(raw_path) == 0:
            outcome, result = "no_output", FinalizeResult(False, None)
        else:
            log.info("Recording of %s done, finalizing %s", cfg.login, raw_path)
            result = finalize_with_sidecars(cfg, raw_path, timeline, duration_ms, self.initial.stream_id,
                                            recorded_at, run=self._run)
            outcome = "completed" if result.ok else "finalize_failed"

        categories = []
        for point in timeline:
            if not categories or categories[-1] != point.category_name:
                categories.append(point.category_name)
        output_bytes = os.path.getsize(result.output_path) if result.output_path and os.path.exists(result.output_path) else None
        em.emit("recording.session", **current.event_fields(), **{
            "outcome": outcome,
            "recording.duration_ms": duration_ms,
            "recording.file": raw_path if not result.ok else None,
            "recording.output": result.output_path,
            "recording.output_bytes": output_bytes,
            "recording.stopped": self._stop_requested,
            "streamlink.exit_code": returncode,
            "ffmpeg.exit_code": result.returncode,
            "session.categories": categories,
            "session.category_changes": self.category_changes,
            "session.title_changes": self.title_changes,
            "session.metadata_changes": self.metadata_changes,
            "session.metadata_poll_errors": self.poll_errors,
            "category.first.name": self.initial.category_name,
            "category.last.name": current.category_name,
        })
        if result.ok:
            _move_events_file(cfg, raw_path)
        return SessionResult(outcome, raw_path, result.output_path, returncode, duration_ms,
                             self.metadata_changes, self._stop_requested, timeline)


def finalize_leftovers(cfg: SessionConfig, emitter: EventEmitter,
                       run: Callable[..., Any] = subprocess.run) -> int:
    """Finalize raw files a previous run left in recorded/ (crash, restart).

    Chapters come from the file's events sidecar when there is one. Returns
    how many files were finalized.
    """
    if not os.path.isdir(cfg.recorded_dir):
        return 0
    done = 0
    for name in sorted(os.listdir(cfg.recorded_dir)):
        raw_path = os.path.join(cfg.recorded_dir, name)
        if not os.path.isfile(raw_path) or not name.endswith(VIDEO_EXTENSIONS):
            continue
        base_raw = os.path.splitext(raw_path)[0]
        events_path = base_raw + EVENTS_SUFFIX
        timeline: list[chapters_mod.TimelinePoint] = []
        started: dict[str, Any] = {}
        if os.path.exists(events_path):
            timeline, started = chapters_mod.read_events_file(events_path)
        duration_ms = max((p.offset_ms for p in timeline), default=0)
        started_at = _parse_rfc3339(str(started.get("ts") or ""))
        if started_at is not None:
            # No session event survived the crash; the last write time bounds the length.
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(raw_path), datetime.timezone.utc)
            duration_ms = max(duration_ms, int((mtime - started_at).total_seconds() * 1000))
        log.info("Processing previously recorded file %s", raw_path)
        leftover_cfg = cfg
        if name.endswith(".mp4") and cfg.container == "mkv":
            # Legacy captures stay MP4; only TS captures are remuxed to MKV.
            leftover_cfg = dataclasses.replace(cfg, container="mp4")
        result = finalize_with_sidecars(leftover_cfg, raw_path, timeline, duration_ms,
                                        str(started.get("stream.id") or ""), str(started.get("ts") or ""), run=run)
        emitter.emit("recording.recovered", **{
            "channel.login": cfg.login,
            "stream.id": started.get("stream.id"),
            "recording.id": started.get("recording.id"),
            "outcome": "completed" if result.ok else "finalize_failed",
            "recording.output": result.output_path,
            "ffmpeg.exit_code": result.returncode,
        })
        if result.ok:
            _move_events_file(cfg, raw_path)
            done += 1
    return done
