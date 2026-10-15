import logging
import types

from recorder import cli
from recorder.events import EventEmitter, ListSink
from recorder.session import SessionResult

from helpers import stream


def test_legacy_flags_still_parse():
    args = cli.build_parser().parse_args(["watch", "-u", "kana", "-q", "720p", "-l", "warn", "--disable-ffmpeg"])
    assert (args.username, args.quality, args.log_level, args.disable_ffmpeg) == ("kana", "720p", logging.WARN, True)
    assert args.container == "mp4"
    assert cli.build_parser().parse_args(["watch", "--logging", "debug"]).log_level == logging.DEBUG


def test_settings_from_config_module_prefers_config_values(monkeypatch):
    monkeypatch.setenv("client_id", "env-id")
    config = types.SimpleNamespace(root_path="/data", username="kana", client_id="", client_secret="s",
                                   twitch_oauth_token="")
    s = cli.Settings.from_config_module(config)
    assert (s.root_path, s.username, s.client_id, s.client_secret) == ("/data", "kana", "env-id", "s")


class Helix:
    def __init__(self, *answers):
        self.answers = list(answers)

    def get_stream(self, login):
        return self.answers.pop(0)


def _record(monkeypatch, helix, results, argv=("record", "--login", "kana")):
    results = list(results)
    parts = []

    class FakeSession:
        def __init__(self, *a, part=0, **kw):
            parts.append(part)

        def request_stop(self):
            pass

        def run(self):
            return results.pop(0)

    monkeypatch.setattr(cli, "RecordingSession", FakeSession)
    sink = ListSink()
    sleeps = []
    code = cli.record(cli.build_parser().parse_args(list(argv)), cli.Settings(), helix, EventEmitter([sink]),
                      sleep=sleeps.append)
    return code, sink, parts, sleeps


def _result(outcome="completed", rc=0, stopped=False):
    return SessionResult(outcome, "raw", "out", rc, 1000, 0, stopped)


def test_record_skips_offline_and_other_broadcasts(monkeypatch):
    code, sink, parts, _ = _record(monkeypatch, Helix(None), [])
    assert code == 0 and sink.events[0]["reason"] == "offline" and parts == []
    code, sink, _, _ = _record(monkeypatch, Helix(stream(stream_id="222")), [],
                               ("record", "--login", "kana", "--stream-id", "111"))
    assert code == 0 and sink.events[0]["reason"] == "stream_mismatch"


def test_record_continues_in_a_new_part_while_broadcast_is_live(monkeypatch):
    # streamlink exits cleanly mid-broadcast (HLS hiccup): the same Job records part 1.
    code, _, parts, _ = _record(monkeypatch, Helix(stream(), stream(), None), [_result(), _result()])
    assert code == 0 and parts == [0, 1]


def test_record_stops_at_a_new_broadcast(monkeypatch):
    code, _, parts, _ = _record(monkeypatch, Helix(stream(), stream(stream_id="999")), [_result()])
    assert code == 0 and parts == [0]


def test_record_asks_job_to_retry_when_pod_stops_mid_stream(monkeypatch):
    code, _, _, _ = _record(monkeypatch, Helix(stream(), stream()), [_result(rc=143, stopped=True)])
    assert code == cli.EXIT_RETRY


def test_record_reports_finalize_failure(monkeypatch):
    code, _, _, _ = _record(monkeypatch, Helix(stream(), None), [_result(outcome="finalize_failed")])
    assert code == 1


def test_record_gives_up_on_repeated_empty_captures(monkeypatch):
    n = cli.MAX_EMPTY_PARTS
    code, _, parts, sleeps = _record(monkeypatch, Helix(*[stream()] * (n + 1)), [_result(outcome="no_output", rc=1)] * n)
    assert code == 0 and len(parts) == n
    assert sleeps == [15, 30, 45, 60, 60][:n]
