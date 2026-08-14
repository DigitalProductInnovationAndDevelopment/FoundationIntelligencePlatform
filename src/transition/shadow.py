"""Bounded asynchronous shadow reads with privacy-safe difference evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import time
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class ShadowRequest:
    """One request replayed against the shadow source for comparison."""
    journey: str
    method: str
    path: str
    query_string: str
    primary_payload: Any
    request_id: str = "unknown"


@dataclass(frozen=True)
class Difference:
    """One recorded difference between the served and shadow payloads."""
    path: str
    kind: str
    primary_fingerprint: str
    shadow_fingerprint: str


@dataclass(frozen=True)
class ComparisonResult:
    """The outcome of comparing a served payload against its shadow."""
    journey: str
    request_id: str
    status: str
    difference_count: int
    differences: tuple[Difference, ...]
    shadow_duration_ms: float
    primary_authoritative: bool = True


@dataclass(frozen=True)
class ComparisonPolicy:
    """Bounds and allowlists governing shadow comparison."""
    unordered_paths: frozenset[str] = frozenset()
    ignored_paths: frozenset[str] = frozenset()
    maximum_differences: int = 100


class ShadowReader(Protocol):
    """Contract for a source that can replay a journey for comparison."""

    async def read(self, request: ShadowRequest) -> Any:
        """Replay one journey and return its payload."""
        ...


class EvidenceSink(Protocol):
    """Contract for recording shadow comparison evidence."""

    def record(self, result: ComparisonResult) -> None:
        """Record one comparison result as evidence."""
        ...


def _json_value(value: Any) -> Any:
    """Coerce a value into a JSON-comparable form."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _fingerprint(value: Any) -> str:
    """Return a stable fingerprint for a payload."""
    encoded = json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _canonical(value: Any, path: str, policy: ComparisonPolicy) -> Any:
    """Canonicalize a payload, sorting approved unordered collections."""
    value = _json_value(value)
    if isinstance(value, dict):
        return {
            key: _canonical(item, f"{path}.{key}", policy)
            for key, item in sorted(value.items())
            if f"{path}.{key}" not in policy.ignored_paths
        }
    if isinstance(value, list):
        canonical = [
            _canonical(item, f"{path}[{index}]", policy)
            for index, item in enumerate(value)
        ]
        if path in policy.unordered_paths:
            canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        return canonical
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return value


def compare_payloads(
    primary: Any,
    shadow: Any,
    *,
    journey: str,
    request_id: str = "unknown",
    policy: ComparisonPolicy = ComparisonPolicy(),
    shadow_duration_ms: float = 0.0,
) -> ComparisonResult:
    """Compare two payloads, returning bounded, privacy-safe differences."""
    left = _canonical(primary, "$", policy)
    right = _canonical(shadow, "$", policy)
    differences: list[Difference] = []

    def walk(primary_value: Any, shadow_value: Any, path: str) -> None:
        if len(differences) >= policy.maximum_differences:
            return
        if type(primary_value) is not type(shadow_value):
            differences.append(
                Difference(path, "type", _fingerprint(primary_value), _fingerprint(shadow_value))
            )
            return
        if isinstance(primary_value, dict):
            all_keys = sorted(set(primary_value) | set(shadow_value))
            for key in all_keys:
                child_path = f"{path}.{key}"
                if key not in primary_value:
                    differences.append(
                        Difference(child_path, "missing_primary", "absent", _fingerprint(shadow_value[key]))
                    )
                elif key not in shadow_value:
                    differences.append(
                        Difference(child_path, "missing_shadow", _fingerprint(primary_value[key]), "absent")
                    )
                else:
                    walk(primary_value[key], shadow_value[key], child_path)
                if len(differences) >= policy.maximum_differences:
                    return
            return
        if isinstance(primary_value, list):
            if len(primary_value) != len(shadow_value):
                differences.append(
                    Difference(path, "length", _fingerprint(len(primary_value)), _fingerprint(len(shadow_value)))
                )
            for index, (primary_item, shadow_item) in enumerate(
                zip(primary_value, shadow_value)
            ):
                walk(primary_item, shadow_item, f"{path}[{index}]")
                if len(differences) >= policy.maximum_differences:
                    return
            return
        if primary_value != shadow_value:
            differences.append(
                Difference(path, "value", _fingerprint(primary_value), _fingerprint(shadow_value))
            )

    walk(left, right, "$")
    return ComparisonResult(
        journey=journey,
        request_id=request_id,
        status="match" if not differences else "difference",
        difference_count=len(differences),
        differences=tuple(differences),
        shadow_duration_ms=round(shadow_duration_ms, 3),
    )


class StructuredLogEvidenceSink:
    """Record paths and fingerprints without serializing potentially sensitive values."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]):
        """Create a sink that writes comparison evidence to the structured log."""
        self._emit = emit

    def record(self, result: ComparisonResult) -> None:
        """Write one comparison result to the structured log."""
        self._emit(
            {
                "event": "shadow_comparison",
                "journey": result.journey,
                "request_id": result.request_id,
                "status": result.status,
                "difference_count": result.difference_count,
                "shadow_duration_ms": result.shadow_duration_ms,
                "primary_authoritative": True,
                "differences": [asdict(item) for item in result.differences],
            }
        )


class ShadowComparisonCoordinator:
    """Schedule shadow work without awaiting it on the primary response path."""

    def __init__(
        self,
        reader: ShadowReader,
        sink: EvidenceSink,
        *,
        policy: ComparisonPolicy,
        timeout_seconds: float,
        maximum_pending: int,
    ):
        """Create a bounded coordinator for asynchronous shadow comparisons."""
        self.reader = reader
        self.sink = sink
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self.maximum_pending = maximum_pending
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        """Return the number of comparisons currently queued."""
        return len(self._tasks)

    def submit(self, request: ShadowRequest) -> bool:
        """Queue a comparison, dropping it when the bound is reached."""
        if len(self._tasks) >= self.maximum_pending:
            self.sink.record(
                ComparisonResult(
                    journey=request.journey,
                    request_id=request.request_id,
                    status="dropped_queue_full",
                    difference_count=0,
                    differences=(),
                    shadow_duration_ms=0.0,
                )
            )
            return False
        task = asyncio.create_task(self._execute(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _execute(self, request: ShadowRequest) -> None:
        """Run one queued comparison and record its evidence."""
        started = time.perf_counter()
        try:
            shadow = await asyncio.wait_for(
                self.reader.read(request), timeout=self.timeout_seconds
            )
            duration = (time.perf_counter() - started) * 1000
            result = compare_payloads(
                request.primary_payload,
                shadow,
                journey=request.journey,
                request_id=request.request_id,
                policy=self.policy,
                shadow_duration_ms=duration,
            )
        except asyncio.TimeoutError:
            result = ComparisonResult(
                request.journey,
                request.request_id,
                "shadow_timeout",
                0,
                (),
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            result = ComparisonResult(
                request.journey,
                request.request_id,
                f"shadow_error_{exc.__class__.__name__}",
                0,
                (),
                (time.perf_counter() - started) * 1000,
            )
        self.sink.record(result)

    async def drain(self) -> None:
        """Wait for queued comparisons to finish, for deterministic tests."""
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class ShadowComparisonMiddleware:
    """Pass response bytes through immediately and compare only after the final body."""

    def __init__(
        self,
        app: Any,
        *,
        coordinator: Optional[ShadowComparisonCoordinator],
        maximum_response_bytes: int,
        journey_resolver: Callable[[str, str, str], Optional[str]],
    ):
        """Wrap an ASGI app so eligible responses are shadow-compared."""
        self.app = app
        self.coordinator = coordinator
        self.maximum_response_bytes = maximum_response_bytes
        self.journey_resolver = journey_resolver

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Serve the request unchanged and queue a comparison out of band."""
        if scope.get("type") != "http" or self.coordinator is None:
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        query_string = scope.get("query_string", b"").decode("utf-8")
        journey = self.journey_resolver(method, path, query_string)
        if journey is None:
            await self.app(scope, receive, send)
            return
        coordinator = self.coordinator
        if coordinator is None:
            await self.app(scope, receive, send)
            return
        response_status = 0
        content_type = ""
        response_request_id = "unknown"
        body = bytearray()
        oversized = False

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal response_status, content_type, response_request_id, oversized
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in message.get("headers", [])
                }
                content_type = headers.get("content-type", "")
                response_request_id = headers.get("x-request-id", "unknown")
            elif message["type"] == "http.response.body" and not oversized:
                chunk = message.get("body", b"")
                if len(body) + len(chunk) <= self.maximum_response_bytes:
                    body.extend(chunk)
                else:
                    oversized = True
                    body.clear()
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and response_status == 200
                and not oversized
                and "application/json" in content_type
            ):
                try:
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return
                headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in scope.get("headers", [])
                }
                coordinator.submit(
                    ShadowRequest(
                        journey=journey,
                        method=method,
                        path=path,
                        query_string=query_string,
                        primary_payload=payload,
                        request_id=(
                            response_request_id
                            if response_request_id != "unknown"
                            else headers.get("x-request-id", "unknown")
                        ),
                    )
                )

        await self.app(scope, receive, send_wrapper)
