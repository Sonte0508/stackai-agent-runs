"""
OpenTelemetry wiring for the service.

We export both traces and metrics over OTLP/HTTP. By default this points at
a local collector (see docker-compose.yml) that forwards to Jaeger, but the
same code works unmodified against Honeycomb / Grafana Cloud / New Relic by
changing OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_EXPORTER_OTLP_HEADERS.

Span/attribute naming loosely follows the OTel Generative AI semantic
conventions (gen_ai.*) where it applies to model-call steps, since that's
the emerging standard other tools (e.g. Honeycomb's GenAI views) already
understand.
"""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

_tracer = None
_meter = None

# Metric instruments, created once and reused across the app.
run_duration_histogram = None
run_counter = None
cost_counter = None
token_counter = None
step_retry_counter = None


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def setup_telemetry(app) -> None:
    """Initialize tracer/meter providers and instrument the FastAPI app."""
    global _tracer, _meter
    global run_duration_histogram, run_counter, cost_counter, token_counter, step_retry_counter

    settings = get_settings()
    resource = Resource.create(
        {
            SERVICE_NAME: settings.service_name,
            "service.version": settings.api_version,
        }
    )
    headers = _parse_headers(settings.otel_exporter_otlp_headers)
    traces_endpoint = f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
    metrics_endpoint = f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/metrics"

    # --- Traces ---
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint, headers=headers))
    )
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer(settings.service_name)

    # --- Metrics ---
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers),
        export_interval_millis=5000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter(settings.service_name)

    run_duration_histogram = _meter.create_histogram(
        name="agent_run_duration_ms",
        unit="ms",
        description="End-to-end duration of a completed agent run",
    )
    run_counter = _meter.create_counter(
        name="agent_run_total",
        description="Number of agent runs, by terminal status",
    )
    cost_counter = _meter.create_counter(
        name="agent_cost_usd_total",
        unit="usd",
        description="Simulated cost accrued by agent runs",
    )
    token_counter = _meter.create_counter(
        name="agent_tokens_total",
        unit="token",
        description="Tokens consumed, by direction (input/output)",
    )
    step_retry_counter = _meter.create_counter(
        name="agent_step_retry_total",
        description="Number of step retries across all runs",
    )

    # Auto-instrument inbound HTTP requests (creates the request-level spans).
    FastAPIInstrumentor.instrument_app(app)


def get_tracer():
    if _tracer is None:
        raise RuntimeError("Telemetry not initialized - call setup_telemetry() first")
    return _tracer
