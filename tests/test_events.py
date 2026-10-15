import io
import json
import logging

import pytest

from recorder.events import (SCHEMA, EventEmitter, JsonlFileSink, ListSink, LogSink, StdoutSink, sinks_from_spec,
                             to_logfmt)


def test_emit_merges_bound_fields_and_drops_none():
    sink = ListSink()
    em = EventEmitter([sink], {"service.name": "svc"}, now=lambda: "T").bind(**{"channel.login": "kana"})
    record = em.emit("stream.metadata_changed", **{"category.name": "VALORANT", "missing": None})
    assert record == {"ts": "T", "event": "stream.metadata_changed", "schema": SCHEMA, "service.name": "svc",
                      "channel.login": "kana", "category.name": "VALORANT"}
    assert sink.events == [record]


def test_failing_sink_does_not_raise_or_block_others():
    class Broken:
        def write(self, event):
            raise OSError("disk full")

    good = ListSink()
    EventEmitter([Broken(), good]).emit("x")
    assert len(good.events) == 1


def test_stdout_sink_writes_one_json_line():
    buf = io.StringIO()
    StdoutSink(buf).write({"event": "x", "category.name": "歌"})
    assert json.loads(buf.getvalue()) == {"event": "x", "category.name": "歌"}
    assert buf.getvalue().count("\n") == 1


def test_jsonl_file_sink_appends(tmp_path):
    path = tmp_path / "a.events.jsonl"
    sink = JsonlFileSink(str(path))
    sink.write({"event": "a"})
    sink.write({"event": "b"})
    assert [json.loads(line)["event"] for line in path.read_text().splitlines()] == ["a", "b"]


def test_log_sink_uses_logfmt(caplog):
    with caplog.at_level(logging.INFO, logger="recorder.events"):
        LogSink().write({"event": "stream.metadata_changed", "category.name": "Just Chatting", "tags": ["a", "b"]})
    assert caplog.messages == ['event=stream.metadata_changed category.name="Just Chatting" tags=a,b']


def test_logfmt_quotes_and_escapes():
    assert to_logfmt({"a": 'say "hi"', "b": True, "c": ""}) == 'a="say \\"hi\\"" b=true c=""'


def test_sinks_from_spec():
    assert [type(s).__name__ for s in sinks_from_spec("stdout, log")] == ["StdoutSink", "LogSink"]
    assert sinks_from_spec("none") == []
    with pytest.raises(ValueError):
        sinks_from_spec("kafka")
