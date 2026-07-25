"""Wide events: one rich, densely-attributed record per notable operation.

Every event carries the full operational context (streamer, stream, file,
encoding and timing details) instead of being spread across several thin log
lines. Each event is emitted twice: as a span event on the active span, so it
shows up inline in the trace, and as a structured log record whose attributes
are exported over OTLP.

Adding a field is just passing another keyword argument -- no consumer needs to
change, because every attribute travels as part of the same flat map.
"""

import logging
import os
import socket
import time

_LOG = logging.getLogger("twitch.recorder.events")

EVENT_DOMAIN = "twitch.recorder"

STREAM_START = "stream_start"
STREAM_END = "stream_end"
RECORDING_COMPLETE = "recording_complete"
RECORDING_FAILED = "recording_failed"
DISK_SPACE_WARNING = "disk_space_warning"
API_RATE_LIMIT = "api_rate_limit"
RECONNECTION_ATTEMPT = "reconnection_attempt"

# Attribute values have to be OTLP primitives; anything else is stringified.
_PRIMITIVES = (str, bool, int, float)


def _coerce(value):
    if isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    return str(value)


class WideEventEmitter:
    """Builds and emits wide events, carrying a shared base context."""

    def __init__(self, context=None):
        self._context = {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "twitch-stream-recorder"),
            "host.name": socket.gethostname(),
        }
        if context:
            self._context.update(context)

    def with_context(self, **fields):
        """Return an emitter that adds `fields` to every event it emits."""
        merged = dict(self._context)
        merged.update(fields)
        return WideEventEmitter(merged)

    def set_context(self, **fields):
        self._context.update(fields)

    def build(self, event_name, **fields):
        attributes = {
            "event.name": event_name,
            "event.domain": EVENT_DOMAIN,
            "event.timestamp": time.time(),
        }
        attributes.update(self._context)
        for key, value in fields.items():
            if value is None:
                continue
            attributes[key.replace("__", ".")] = _coerce(value)
        return attributes

    def emit(self, event_name, level=logging.INFO, message=None, **fields):
        """Record a wide event on the current span and as a structured log."""
        attributes = self.build(event_name, **fields)

        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span is not None:
                span.add_event(event_name, attributes=attributes)
        except ImportError:
            pass

        _LOG.log(level, message or event_name, extra=attributes)
        return attributes
