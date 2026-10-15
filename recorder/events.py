"""Wide events.

Every event is one flat JSON object: a timestamp, an event name, a schema
version, and every piece of context we have at that moment (channel, stream,
recording, category, pod). Flat dotted keys load straight into Loki's `| json`
parser, ClickHouse, or a Parquet column per key, without a nested schema.

Sinks decide where an event goes:

- ``stdout``: one JSON object per line. In Kubernetes this is the log stream
  that Loki/Alloy/Vector already collect, so no extra exporter is needed.
- ``log``: the same fields as one logfmt line through the ``logging`` module,
  for setups that only keep the log file.
- ``JsonlFileSink``: an ``*.events.jsonl`` sidecar next to the recording. It
  travels with the video, so chapters can be rebuilt from it after a crash.

A sink that fails is logged and skipped: losing an event must never stop a
recording.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from typing import Any, Callable, Iterable, Protocol

from recorder import __version__

SCHEMA = "twitch-recorder.event/v1"

log = logging.getLogger(__name__)


class EventSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


class StdoutSink:
    def __init__(self, stream=None):
        self._stream = stream

    def write(self, event: dict[str, Any]) -> None:
        stream = self._stream or sys.stdout
        stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


class LogSink:
    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO):
        self._logger = logger or logging.getLogger("recorder.events")
        self._level = level

    def write(self, event: dict[str, Any]) -> None:
        self._logger.log(self._level, to_logfmt(event))


class JsonlFileSink:
    def __init__(self, path: str):
        self.path = path

    def write(self, event: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


class ListSink:
    """Collects events in memory (tests, and callers that inspect what was sent)."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _utcnow() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventEmitter:
    def __init__(self, sinks: Iterable[EventSink], base: dict[str, Any] | None = None,
                 now: Callable[[], str] = _utcnow):
        self._sinks = list(sinks)
        self._base = dict(base or {})
        self._now = now

    def bind(self, *extra_sinks: EventSink, **fields: Any) -> "EventEmitter":
        """Child emitter with more context fields and, optionally, more sinks."""
        return EventEmitter(self._sinks + list(extra_sinks), {**self._base, **fields}, self._now)

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": self._now(), "event": event, "schema": SCHEMA, **self._base, **fields}
        record = {k: v for k, v in record.items() if v is not None}
        for sink in self._sinks:
            try:
                sink.write(record)
            except Exception as e:  # noqa: BLE001 - a broken sink must not break recording
                log.warning("Event sink %s failed for %s: %s", type(sink).__name__, event, e)
        return record


def runtime_fields(service_name: str = "twitch-stream-recorder") -> dict[str, Any]:
    """Process and Kubernetes context, from the downward API env when present."""
    return {
        "service.name": service_name,
        "service.version": __version__,
        "k8s.namespace.name": os.environ.get("POD_NAMESPACE"),
        "k8s.pod.name": os.environ.get("POD_NAME"),
        "k8s.node.name": os.environ.get("NODE_NAME"),
    }


def sinks_from_spec(spec: str) -> list[EventSink]:
    """Build sinks from a comma list such as ``stdout`` or ``stdout,log``."""
    sinks: list[EventSink] = []
    for name in (s.strip().lower() for s in spec.split(",")):
        if not name or name == "none":
            continue
        if name == "stdout":
            sinks.append(StdoutSink())
        elif name == "log":
            sinks.append(LogSink())
        else:
            raise ValueError(f"Unknown event sink: {name!r} (expected stdout, log, or none)")
    return sinks


def _logfmt_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    elif isinstance(value, bool):
        value = "true" if value else "false"
    text = str(value)
    if text == "" or any(c in text for c in ' ="\\\n\t'):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return text


def to_logfmt(event: dict[str, Any]) -> str:
    return " ".join(f"{k}={_logfmt_value(v)}" for k, v in event.items())
