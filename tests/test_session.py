import datetime
import json
import os

import requests

from recorder.events import EventEmitter, ListSink
from recorder.session import RecordingSession, SessionConfig, finalize_leftovers

from helpers import FakeClock, FakeFfmpeg, FakeHelix, FakeProc, stream

WALL = datetime.datetime(2026, 10, 15, 8, 0, 5, tzinfo=datetime.timezone.utc)


def make_session(tmp_path, helix, container="mkv", ffmpeg=None, waits=3, returncode=0):
    clock = FakeClock()
    cfg = SessionConfig(login="kana", root_path=str(tmp_path), container=container, metadata_interval=30)
    sink = ListSink()
    procs = []

    def popen(args):
        procs.append(FakeProc(args, clock, waits, returncode))
        return procs[-1]

    session = RecordingSession(cfg, helix, EventEmitter([sink]), stream(), clock=clock,
                               wall_clock=lambda: WALL, popen=popen, run=ffmpeg or FakeFfmpeg())
    return session, sink, procs


def test_category_change_becomes_event_chapter_and_session_summary(tmp_path):
    game = stream(category=("516575", "VALORANT"), title="Ranked", tags=("FPS",))
    helix = FakeHelix([stream(), game, game])
    ffmpeg = FakeFfmpeg()
    session, sink, _ = make_session(tmp_path, helix, ffmpeg=ffmpeg)

    result = session.run()

    names = [e["event"] for e in sink.events]
    assert names == ["recording.started", "stream.metadata_changed", "recording.session"]
    change = sink.events[1]
    assert change["category.before.name"] == "Music"
    assert change["category.name"] == "VALORANT"
    assert change["recording.offset_ms"] == 60_000  # second poll, 2 x 30 s after start
    assert change["stream.tags.removed"] == ["English", "Singing"]

    summary = sink.events[2]
    assert summary["outcome"] == "completed"
    assert summary["session.categories"] == ["Music", "VALORANT"]
    assert summary["session.category_changes"] == 1
    assert summary["category.last.name"] == "VALORANT"
    assert summary["recording.duration_ms"] == 90_000

    # Raw capture removed only after a good remux; sidecars land next to the MKV.
    processed = tmp_path / "processed" / "kana"
    assert result.output_path.endswith(".mkv")
    assert not os.listdir(tmp_path / "recorded" / "kana")
    base = os.path.splitext(result.output_path)[0]
    chapters = json.loads(open(base + ".chapters.json").read())["chapters"]
    assert [(c["title"], c["start_ms"], c["end_ms"]) for c in chapters] == [("Music", 0, 60_000),
                                                                            ("VALORANT", 60_000, 90_000)]
    events_file = [json.loads(line)["event"] for line in open(base + ".events.jsonl")]
    assert events_file == names
    assert sorted(os.listdir(processed)) == sorted(os.path.basename(base) + s
                                                   for s in (".mkv", ".chapters.json", ".events.jsonl"))
    cmd = ffmpeg.calls[0]
    assert cmd[cmd.index("-map_chapters") + 1] == "1"


def test_failed_remux_keeps_raw_file(tmp_path):
    session, sink, _ = make_session(tmp_path, FakeHelix([stream()]), ffmpeg=FakeFfmpeg(returncode=1), waits=0)
    result = session.run()
    assert result.outcome == "finalize_failed"
    assert os.path.exists(result.raw_path)
    assert sink.events[-1]["recording.file"] == result.raw_path


def test_metadata_poll_errors_do_not_stop_recording(tmp_path):
    helix = FakeHelix([requests.ConnectionError("boom")])
    session, sink, _ = make_session(tmp_path, helix, waits=2)
    result = session.run()
    assert result.outcome == "completed"
    assert sink.events[-1]["session.metadata_poll_errors"] == 2


def test_legacy_mp4_mode_keeps_plain_remux(tmp_path):
    ffmpeg = FakeFfmpeg()
    session, _, procs = make_session(tmp_path, FakeHelix([stream()]), container="mp4", ffmpeg=ffmpeg, waits=0)
    result = session.run()
    assert result.output_path.endswith(".mp4")
    assert "-map_chapters" not in ffmpeg.calls[0]
    assert procs[0].args[:2] == ["streamlink", "--twitch-disable-ads"]


def test_empty_oauth_token_is_not_sent(tmp_path):
    session, _, _ = make_session(tmp_path, FakeHelix([stream()]), waits=0)
    session.cfg.oauth_token = ""
    session.cfg.__post_init__()
    assert "--twitch-api-header" not in session.streamlink_args("out.ts")


def test_request_stop_terminates_streamlink(tmp_path):
    session, sink, procs = make_session(tmp_path, FakeHelix([stream()]), waits=100)
    orig_poll = session._poll_metadata

    def poll_then_stop(*a):
        session.request_stop()
        return orig_poll(*a)

    session._poll_metadata = poll_then_stop
    result = session.run()
    assert procs[0].terminated
    assert result.stopped and sink.events[-1]["recording.stopped"] is True


def test_leftover_rebuilds_chapters_from_events_sidecar(tmp_path):
    rec = tmp_path / "recorded" / "kana"
    rec.mkdir(parents=True)
    (rec / "kana - x.ts").write_bytes(b"video")
    events = [
        {"event": "recording.started", "ts": "2026-10-15T08:00:00.000Z", "recording.offset_ms": 0,
         "category.id": "1", "category.name": "Music", "stream.id": "111"},
        {"event": "stream.metadata_changed", "recording.offset_ms": 5000, "category.id": "2",
         "category.name": "VALORANT"},
    ]
    (rec / "kana - x.events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    sink = ListSink()
    cfg = SessionConfig(login="kana", root_path=str(tmp_path), container="mkv")

    assert finalize_leftovers(cfg, EventEmitter([sink]), run=FakeFfmpeg()) == 1

    processed = tmp_path / "processed" / "kana"
    chapters = json.loads((processed / "kana - x.chapters.json").read_text())["chapters"]
    assert [c["title"] for c in chapters] == ["Music", "VALORANT"]
    assert (processed / "kana - x.events.jsonl").exists()
    assert sink.events[0]["event"] == "recording.recovered" and sink.events[0]["outcome"] == "completed"
    assert os.listdir(rec) == []


def test_later_parts_get_distinct_file_names(tmp_path):
    first, _, _ = make_session(tmp_path, FakeHelix([stream()]), waits=0)
    second, _, _ = make_session(tmp_path, FakeHelix([stream()]), waits=0)
    second.part = 1
    a, b = first.run(), second.run()  # same wall clock second
    assert a.output_path != b.output_path and b.output_path.endswith(" - part1.mkv")
    assert os.path.exists(a.output_path) and os.path.exists(b.output_path)
