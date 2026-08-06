"""Dependency-free metric/alarm definitions with a bounded local registry."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any, Mapping


OBSERVABILITY_CONFIGURATION_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "observability.json"
)
REQUIRED_METRICS = frozenset(
    {
        "api_request_duration_ms",
        "readiness_success",
        "api_errors_total",
        "db_pool_checked_out",
        "db_pool_utilization_ratio",
        "query_duration_ms",
        "cache_hit_ratio",
        "dataset_age_seconds",
        "source_freshness_seconds",
        "pipeline_duration_ms",
        "pipeline_failures_total",
        "pipeline_record_count",
        "reconciliation_mismatch_count",
        "conversion_gap_count",
        "programme_coverage_ratio",
        "geography_coverage_ratio",
        "queue_oldest_message_age_seconds",
        "dlq_depth",
        "worker_failures_total",
        "task_restarts_total",
        "estimated_cost_usd",
    }
)
REQUIRED_ALARMS = frozenset(
    {
        "readiness-failure",
        "api-5xx-budget",
        "api-latency-budget",
        "stale-data",
        "pipeline-failure",
        "reconciliation-mismatch",
        "dlq-messages",
        "queue-backlog",
        "conversion-gap-increase",
        "programme-coverage-decrease",
        "geography-coverage-decrease",
        "cost-threshold",
        "rds-cpu",
        "rds-connections",
        "rds-storage",
    }
)


@dataclass(frozen=True)
class MetricDefinition:
    """One versioned metric definition."""
    name: str
    type: str
    unit: str
    dimensions: tuple[str, ...]

    def validate(self) -> None:
        """Reject a metric whose type or unit is unsupported."""
        if self.type not in {"counter", "gauge", "histogram"}:
            raise ValueError(f"Invalid metric type for {self.name}")
        if not self.name.strip() or not self.unit.strip():
            raise ValueError("Metric name and unit are required")


@dataclass(frozen=True)
class AlarmDefinition:
    """One versioned alarm definition and its threshold."""
    name: str
    metric: str
    comparison: str
    threshold: float
    evaluation_periods: int
    period_seconds: int
    runbook: str

    def validate(self) -> None:
        """Reject an alarm missing a metric, comparison or threshold."""
        if self.comparison not in {
            "GreaterThanThreshold",
            "GreaterThanOrEqualToThreshold",
            "LessThanThreshold",
            "LessThanOrEqualToThreshold",
        }:
            raise ValueError(f"Invalid alarm comparison for {self.name}")
        if self.evaluation_periods < 1 or self.period_seconds < 10:
            raise ValueError(f"Invalid alarm evaluation window for {self.name}")
        if not self.runbook.strip():
            raise ValueError(f"Alarm runbook is required for {self.name}")


@dataclass(frozen=True)
class ObservabilityConfiguration:
    """The versioned telemetry contract for this service."""
    service: str
    expected_schema_version: str
    metrics: tuple[MetricDefinition, ...]
    alarms: tuple[AlarmDefinition, ...]

    def validate(self) -> None:
        """Reject a configuration whose alarms reference unknown metrics."""
        if not self.service.strip() or not self.expected_schema_version.strip():
            raise ValueError("Service and expected schema version are required")
        for definition in self.metrics:
            definition.validate()
        for alarm in self.alarms:
            alarm.validate()
        metric_names = {definition.name for definition in self.metrics}
        alarm_names = {definition.name for definition in self.alarms}
        missing_metrics = sorted(REQUIRED_METRICS - metric_names)
        missing_alarms = sorted(REQUIRED_ALARMS - alarm_names)
        if missing_metrics:
            raise ValueError(f"Missing metrics: {', '.join(missing_metrics)}")
        if missing_alarms:
            raise ValueError(f"Missing alarms: {', '.join(missing_alarms)}")
        unknown_alarm_metrics = sorted(
            {
                alarm.metric
                for alarm in self.alarms
                if alarm.metric not in metric_names and not alarm.metric.startswith("aws_")
            }
        )
        if unknown_alarm_metrics:
            raise ValueError(
                f"Alarms reference undefined metrics: {', '.join(unknown_alarm_metrics)}"
            )
        if len(metric_names) != len(self.metrics) or len(alarm_names) != len(self.alarms):
            raise ValueError("Metric and alarm names must be unique")


def load_observability_configuration(
    path: Path = OBSERVABILITY_CONFIGURATION_PATH,
) -> ObservabilityConfiguration:
    """Load and validate the versioned observability configuration file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("configuration_version") != "1":
        raise ValueError("Unsupported observability configuration version")
    configuration = ObservabilityConfiguration(
        service=str(payload["service"]),
        expected_schema_version=str(payload["expected_schema_version"]),
        metrics=tuple(
            MetricDefinition(
                name=str(entry["name"]),
                type=str(entry["type"]),
                unit=str(entry["unit"]),
                dimensions=tuple(entry.get("dimensions", ())),
            )
            for entry in payload.get("metrics", [])
        ),
        alarms=tuple(
            AlarmDefinition(
                name=str(entry["name"]),
                metric=str(entry["metric"]),
                comparison=str(entry["comparison"]),
                threshold=float(entry["threshold"]),
                evaluation_periods=int(entry["evaluation_periods"]),
                period_seconds=int(entry["period_seconds"]),
                runbook=str(entry["runbook"]),
            )
            for entry in payload.get("alarms", [])
        ),
    )
    configuration.validate()
    return configuration


class MetricsRegistry:
    """Bounded process-local evidence; production publishing uses CloudWatch."""

    def __init__(self, configuration: ObservabilityConfiguration):
        """Create a bounded in-process registry for the declared metrics."""
        configuration.validate()
        self.configuration = configuration
        self._definitions = {definition.name: definition for definition in configuration.metrics}
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(name: str, dimensions: Mapping[str, object]) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Build the storage key for a metric and its label set."""
        return name, tuple(sorted((str(key), str(value)) for key, value in dimensions.items()))

    def _validate(self, name: str, dimensions: Mapping[str, object]) -> MetricDefinition:
        """Reject a sample that does not match a declared metric."""
        definition = self._definitions.get(name)
        if definition is None:
            raise ValueError(f"Unknown metric: {name}")
        allowed = set(definition.dimensions)
        if set(dimensions) - allowed:
            raise ValueError(f"Unexpected dimensions for metric {name}")
        return definition

    def increment(self, name: str, value: float = 1.0, **dimensions: object) -> None:
        """Increment a declared counter."""
        definition = self._validate(name, dimensions)
        if definition.type != "counter" or value < 0:
            raise ValueError(f"Metric {name} is not a non-negative counter")
        key = self._key(name, dimensions)
        with self._lock:
            record = self._values.setdefault(key, {"value": 0.0})
            record["value"] += float(value)

    def set_gauge(self, name: str, value: float, **dimensions: object) -> None:
        """Set a declared gauge to an absolute value."""
        definition = self._validate(name, dimensions)
        if definition.type != "gauge":
            raise ValueError(f"Metric {name} is not a gauge")
        with self._lock:
            self._values[self._key(name, dimensions)] = {"value": float(value)}

    def observe(self, name: str, value: float, **dimensions: object) -> None:
        """Record one observation against a declared histogram."""
        definition = self._validate(name, dimensions)
        if definition.type != "histogram" or value < 0:
            raise ValueError(f"Metric {name} is not a non-negative histogram")
        key = self._key(name, dimensions)
        with self._lock:
            record = self._values.setdefault(
                key,
                {"count": 0.0, "sum": 0.0, "min": float(value), "max": float(value)},
            )
            record["count"] += 1.0
            record["sum"] += float(value)
            record["min"] = min(record["min"], float(value))
            record["max"] = max(record["max"], float(value))

    def snapshot(self) -> list[dict[str, Any]]:
        """Return bounded local evidence alongside the metric definitions."""
        with self._lock:
            return [
                {
                    "name": name,
                    "dimensions": dict(dimensions),
                    **values,
                }
                for (name, dimensions), values in sorted(self._values.items())
            ]
