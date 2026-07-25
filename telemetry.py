"""OpenTelemetry bootstrap for the twitch recorder.

Everything in here is optional: if the OpenTelemetry packages are missing, or
telemetry is disabled by configuration, the module hands back no-op objects so
callers never need to guard their instrumentation calls.
"""

import atexit
import logging
import os
import socket
import time

_LOG = logging.getLogger(__name__)

SERVICE_VERSION = "1.0.0"
INSTRUMENTATION_NAME = "twitch-stream-recorder"


def _env_flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class _NoOpSpan:
    def set_attribute(self, *_args, **_kwargs):
        pass

    def set_attributes(self, *_args, **_kwargs):
        pass

    def add_event(self, *_args, **_kwargs):
        pass

    def record_exception(self, *_args, **_kwargs):
        pass

    def set_status(self, *_args, **_kwargs):
        pass

    def is_recording(self):
        return False

    def end(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _NoOpTracer:
    def start_as_current_span(self, *_args, **_kwargs):
        return _NoOpSpan()

    def start_span(self, *_args, **_kwargs):
        return _NoOpSpan()


class _NoOpInstrument:
    def add(self, *_args, **_kwargs):
        pass

    def record(self, *_args, **_kwargs):
        pass


class _NoOpMeter:
    def create_counter(self, *_args, **_kwargs):
        return _NoOpInstrument()

    def create_up_down_counter(self, *_args, **_kwargs):
        return _NoOpInstrument()

    def create_histogram(self, *_args, **_kwargs):
        return _NoOpInstrument()

    def create_observable_gauge(self, *_args, **_kwargs):
        return _NoOpInstrument()


class Telemetry:
    """Holds the tracer plus every metric instrument used by the recorder."""

    def __init__(self, enabled, tracer, meter):
        self.enabled = enabled
        self.tracer = tracer
        self._meter = meter

        self.active_recordings = meter.create_up_down_counter(
            "twitch.recorder.active_recordings",
            unit="{recording}",
            description="Number of stream recordings currently in progress",
        )
        self.recording_duration = meter.create_histogram(
            "twitch.recorder.recording.duration",
            unit="s",
            description="Wall-clock duration of a completed stream recording",
        )
        self.bytes_written = meter.create_counter(
            "twitch.recorder.bytes_written",
            unit="By",
            description="Total bytes written to disk by finished recordings",
        )
        self.errors = meter.create_counter(
            "twitch.recorder.errors",
            unit="{error}",
            description="Errors encountered, partitioned by error.type",
        )
        self.api_requests = meter.create_counter(
            "twitch.recorder.api.requests",
            unit="{request}",
            description="Twitch API calls, partitioned by outcome",
        )
        self.processing_duration = meter.create_histogram(
            "twitch.recorder.processing.duration",
            unit="s",
            description="Duration of ffmpeg post-processing of a recorded file",
        )

        # Stream uptime is sampled on export rather than pushed, so it stays
        # correct even while a single long recording is in flight.
        self._uptime_sources = {}
        meter.create_observable_gauge(
            "twitch.recorder.stream.uptime",
            callbacks=[self._observe_uptime],
            unit="s",
            description="Seconds elapsed since the current recording started",
        )

    def track_stream_start(self, key, attributes):
        self._uptime_sources[key] = (time.time(), dict(attributes))

    def track_stream_end(self, key):
        self._uptime_sources.pop(key, None)

    def _observe_uptime(self, _options):
        try:
            from opentelemetry.metrics import Observation
        except ImportError:
            return []
        now = time.time()
        return [
            Observation(now - started, attributes)
            for started, attributes in list(self._uptime_sources.values())
        ]


def _build_resource():
    from opentelemetry.sdk.resources import Resource

    # Resource.create merges OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME;
    # the explicit values below only fill in what the environment omitted.
    return Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", INSTRUMENTATION_NAME),
            "service.version": SERVICE_VERSION,
            "host.name": socket.gethostname(),
        }
    )


def _use_http_protocol():
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").strip().lower()
    return protocol.startswith("http")


def _exporters():
    """Return (span, metric, log) exporter classes for the configured protocol."""
    if _use_http_protocol():
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    return OTLPSpanExporter, OTLPMetricExporter, OTLPLogExporter


def _init_tracing(resource, span_exporter_cls):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(span_exporter_cls()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)
    return trace.get_tracer(INSTRUMENTATION_NAME, SERVICE_VERSION)


def _init_metrics(resource, metric_exporter_cls):
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    interval_ms = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "15000"))
    reader = PeriodicExportingMetricReader(
        metric_exporter_cls(), export_interval_millis=interval_ms
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    atexit.register(provider.shutdown)
    return metrics.get_meter(INSTRUMENTATION_NAME, SERVICE_VERSION)


def _init_logging(resource, log_exporter_cls):
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter_cls()))
    set_logger_provider(provider)
    atexit.register(provider.shutdown)

    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    logging.getLogger().addHandler(handler)


def _init_auto_instrumentation():
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
    except ImportError:
        _LOG.debug("requests auto-instrumentation unavailable")

    try:
        from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

        SystemMetricsInstrumentor().instrument()
    except ImportError:
        _LOG.debug("runtime/system metrics instrumentation unavailable")


def _disabled_telemetry(reason):
    _LOG.info("OpenTelemetry disabled: %s", reason)
    return Telemetry(False, _NoOpTracer(), _NoOpMeter())


def init_telemetry():
    """Configure the OTel SDK, returning a Telemetry facade.

    Telemetry is enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set, and can be
    forced on or off with OTEL_ENABLED regardless of the endpoint.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not _env_flag("OTEL_ENABLED", bool(endpoint)):
        return _disabled_telemetry("set OTEL_EXPORTER_OTLP_ENDPOINT to enable")

    try:
        resource = _build_resource()
        span_exporter, metric_exporter, log_exporter = _exporters()
        tracer = _init_tracing(resource, span_exporter)
        meter = _init_metrics(resource, metric_exporter)
        if _env_flag("OTEL_LOGS_ENABLED", True):
            _init_logging(resource, log_exporter)
        _init_auto_instrumentation()
    except Exception as e:  # noqa: BLE001 - telemetry must never break recording
        return _disabled_telemetry("initialization failed: %s" % e)

    _LOG.info(
        "OpenTelemetry initialized, exporting to %s over %s",
        endpoint or "the OTLP default endpoint",
        "http" if _use_http_protocol() else "grpc",
    )
    return Telemetry(True, tracer, meter)
