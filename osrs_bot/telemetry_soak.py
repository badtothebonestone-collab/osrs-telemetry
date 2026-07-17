from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import statistics
import sys
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .configuration import DEFAULT_RUNTIME_CONFIG
from .engine_frame import EngineFramePublisher, EngineStage
from .model import Action, ActionKind, Observation
from .observability import TimingPhase
from .observation import (
    MAX_SCENE_OBJECT_ROWS,
    ObservationBackpressureError,
    ObservationSchemaError,
    parse_observation,
)
from .runtime import TaskRuntime
from .task import WoodcutBankTask
from .task_contract import Decision, ObservationRequest, TaskSnapshot, TaskStatus


SOAK_SCHEMA = "telemetry_pipeline_soak.v1"
DEFAULT_FIXTURE = Path("tests/fixtures/snapshot_loaded.json")
CONCURRENCY_LEVELS = (1, 2, 4, 8)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires samples")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _distribution(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50Millis": _percentile(values, 0.50),
        "p95Millis": _percentile(values, 0.95),
        "p99Millis": _percentile(values, 0.99),
        "maxMillis": max(values),
        "meanMillis": statistics.fmean(values),
    }


def _measure(samples: int, operation: Callable[[], object]) -> dict[str, float | int]:
    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        operation()
        durations.append((time.perf_counter() - started) * 1000.0)
    return _distribution(durations)


def _rss_bytes() -> int | None:
    if os.name != "nt":
        try:
            import resource

            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
        except (ImportError, OSError, ValueError):
            return None

    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if ok else None
    except (AttributeError, OSError, ValueError):
        return None


def _observation_signature(observation: Observation) -> tuple[object, ...]:
    return (
        observation.tick,
        observation.frame_id,
        observation.geometry_frame_id,
        len(observation.nearby_objects),
        observation.scene_census.parsed_object_count,
    )


def _concurrency_probe(
    payload: object,
    *,
    calls_per_level: int,
) -> dict[str, object]:
    baseline_threads = threading.active_count()
    levels: list[dict[str, object]] = []
    for workers in CONCURRENCY_LEVELS:
        active_high_water = baseline_threads
        active_lock = threading.Lock()

        def parse_once() -> tuple[tuple[object, ...], float]:
            nonlocal active_high_water
            with active_lock:
                active_high_water = max(active_high_water, threading.active_count())
            started = time.perf_counter()
            observation = parse_observation(payload)
            return _observation_signature(observation), (
                time.perf_counter() - started
            ) * 1000.0

        rss_before = _rss_bytes()
        wall_started = time.perf_counter()
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"telemetry-soak-{workers}",
        ) as executor:
            results = list(executor.map(lambda _index: parse_once(), range(calls_per_level)))
        wall_millis = (time.perf_counter() - wall_started) * 1000.0
        signatures = {signature for signature, _duration in results}
        if len(signatures) != 1:
            raise RuntimeError("concurrent parsing produced cross-request contamination")
        durations = [duration for _signature, duration in results]
        levels.append(
            {
                "workers": workers,
                "calls": calls_per_level,
                "latency": _distribution(durations),
                "wallMillis": wall_millis,
                "throughputPerSecond": calls_per_level / max(wall_millis / 1000.0, 1e-9),
                "threadCountBefore": baseline_threads,
                "threadCountHighWater": active_high_water,
                "threadCountAfter": threading.active_count(),
                "rssBytesBefore": rss_before,
                "rssBytesAfter": _rss_bytes(),
                "uniqueResultSignatures": len(signatures),
            }
        )
    return {
        "schema": "telemetry_pipeline_concurrency_soak.v1",
        "evidenceKind": "synthetic",
        "levels": levels,
    }


class _WaitTask:
    def observation_request(self) -> ObservationRequest:
        return ObservationRequest()

    def decide(self, observation: Observation) -> Decision:
        return Decision(
            "soak_wait",
            "synthetic soak observation accepted",
            Action(ActionKind.WAIT, "Wait", observation.tick),
        )

    def apply_verification(self, _result: object) -> None:
        raise AssertionError("wait-only soak task cannot verify")

    def discard_pending_action(
        self,
        _reason: str,
        *,
        target_invalidated: bool = True,
    ) -> None:
        raise AssertionError("wait-only soak task has no pending action")

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("telemetry-soak", TaskStatus.RUNNING, "soak_wait")


class _BackpressureClient:
    def __init__(self, busy_count: int, observation: Observation | None) -> None:
        self.busy_count = busy_count
        self.observation = observation
        self.fetch_count = 0

    def fetch(self, _tiles: object, _priority_ids: object = ()) -> Observation:
        self.fetch_count += 1
        if self.fetch_count <= self.busy_count:
            raise ObservationBackpressureError(503, "endpoint_busy")
        if self.observation is None:
            raise AssertionError("bounded storm should terminate before a payload is needed")
        return self.observation


def _backpressure_probe(observation: Observation) -> dict[str, object]:
    def run_case(busy_count: int, include_observation: bool) -> dict[str, object]:
        client = _BackpressureClient(
            busy_count,
            observation if include_observation else None,
        )
        publisher = EngineFramePublisher()
        wait_states: list[str] = []
        backpressure_timing: list[dict[str, int]] = []

        def retain_frame_evidence(frame: object) -> None:
            observability = frame.observability
            wait_states.append(
                observability.wait_state.value
                if observability.wait_state is not None
                else "NONE"
            )
            aggregate = observability.timing.for_phase(
                TimingPhase.ENDPOINT_BACKPRESSURE_WAIT
            )
            if aggregate is not None:
                backpressure_timing.append(aggregate.to_dict())

        publisher.subscribe(retain_frame_evidence)
        sleeps: list[float] = []
        runtime = TaskRuntime(
            client,
            _WaitTask(),
            verifier=object(),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=1),
            frame_publisher=publisher,
            sleep=sleeps.append,
        )
        result = runtime.run()
        return {
            "busyResponses": busy_count,
            "fetchCount": client.fetch_count,
            "sleepCount": len(sleeps),
            "resultStatus": result.status,
            "observations": result.observations,
            "waitStates": wait_states,
            "backpressureWaitTiming": (
                backpressure_timing[-1] if backpressure_timing else None
            ),
            "terminalReason": result.reason,
        }

    recovery = run_case(8, True)
    storm = run_case(9, False)
    if recovery["observations"] != 1 or recovery["resultStatus"] != "LIMIT":
        raise RuntimeError("bounded endpoint backpressure recovery failed")
    if storm["observations"] != 0 or storm["resultStatus"] != "ERROR":
        raise RuntimeError("endpoint backpressure storm was not bounded")
    return {
        "schema": "telemetry_pipeline_queue_behavior.v1",
        "evidenceKind": "synthetic",
        "retryBudget": 8,
        "recoveryAtBudget": recovery,
        "stormBeyondBudget": storm,
    }


def _frame_publication_probe(samples: int) -> dict[str, float | int]:
    publisher = EngineFramePublisher()
    task = TaskSnapshot("telemetry-soak", TaskStatus.RUNNING, "publish")
    return _measure(
        samples,
        lambda: publisher.publish(stage=EngineStage.OBSERVED, task=task),
    )


def _target_decision_probe(
    observation: Observation,
    samples: int,
) -> dict[str, object]:
    task = WoodcutBankTask()
    supported_ids = task.definition.resource.selector.object_ids
    target = next(
        (
            item
            for item in observation.nearby_objects
            if item.object_id in supported_ids
            and item.name == task.definition.resource.selector.name
            and item.supports(task.definition.resource.selector.action)
        ),
        None,
    )
    irrelevant_source = next(
        (
            item
            for item in observation.nearby_objects
            if target is not None and item.key != target.key
        ),
        None,
    )
    if target is None or irrelevant_source is None:
        raise RuntimeError("fixture must contain one resource and one irrelevant row")
    target = replace(
        target,
        key="soak-target:1276:3196:3244:0",
        location=task.definition.resource.work_area.anchor,
        distance=1,
        scene_x=49,
        scene_y=52,
    )
    irrelevant = tuple(
        replace(
            irrelevant_source,
            key=f"soak-irrelevant:{index:04d}",
            object_id=900_000 + index,
            name="Irrelevant object",
            actions=("Examine",),
            distance=index + 2,
        )
        for index in range(1_000)
    )
    dense_observation = replace(
        observation,
        nearby_objects=(target, *irrelevant),
    )
    latency = _measure(
        samples,
        lambda: task._classify_trees(dense_observation),
    )
    metrics = task.last_resource_selection_metrics
    if metrics["scene_objects"] != 1_001:
        raise RuntimeError("target decision probe did not retain the dense scene")
    if metrics["identity_evaluations"] > 33:
        raise RuntimeError("target decision identity evaluations exceeded the bound")
    return {
        "sceneObjects": 1_001,
        "latency": latency,
        "operationCounts": metrics,
    }


def run_soak(
    *,
    fixture_path: Path,
    output_directory: Path,
    samples: int,
    concurrency_samples: int,
) -> dict[str, object]:
    fixture_bytes = fixture_path.read_bytes()
    payload = json.loads(fixture_bytes)
    observation = parse_observation(payload)

    rss_before = _rss_bytes()
    thread_count_before = threading.active_count()
    warm_parse = _measure(samples, lambda: parse_observation(payload))
    decode_and_parse = _measure(
        samples,
        lambda: parse_observation(json.loads(fixture_bytes)),
    )

    oversized = json.loads(fixture_bytes)
    census = oversized["payloads"]["scene_object_census"]
    source_rows = list(census.get("objects", []))
    if not source_rows:
        raise RuntimeError("fixture must contain a scene object row")
    census["objects"] = [source_rows[0]] * (MAX_SCENE_OBJECT_ROWS + 1)

    def reject_oversized() -> None:
        try:
            parse_observation(oversized)
        except ObservationSchemaError:
            return
        raise AssertionError("oversized scene was not rejected")

    oversized_rejection = _measure(samples, reject_oversized)

    tracemalloc.start()
    for _ in range(samples):
        parse_observation(payload)
    traced_current, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    concurrency = _concurrency_probe(
        payload,
        calls_per_level=concurrency_samples,
    )
    queue_behavior = _backpressure_probe(observation)
    final_metrics = {
        "schema": "telemetry_pipeline_metrics.v1",
        "evidenceKind": "synthetic",
        "fixture": str(fixture_path),
        "fixtureBytes": len(fixture_bytes),
        "observationSignature": list(_observation_signature(observation)),
        "warmParse": warm_parse,
        "decodeAndParse": decode_and_parse,
        "oversizedRejection": oversized_rejection,
        "engineFramePublication": _frame_publication_probe(samples),
        "targetDecision1001Rows": _target_decision_probe(observation, samples),
        "memory": {
            "rssBytesBefore": rss_before,
            "rssBytesAfter": _rss_bytes(),
            "tracemallocCurrentBytes": traced_current,
            "tracemallocPeakBytes": traced_peak,
        },
        "threads": {
            "before": thread_count_before,
            "after": threading.active_count(),
        },
    }
    summary = {
        "schema": SOAK_SCHEMA,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "evidenceKind": "synthetic",
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processorCount": os.cpu_count(),
        },
        "samples": samples,
        "concurrencySamplesPerLevel": concurrency_samples,
        "scenarioCoverage": {
            "ordinaryFixture": True,
            "oversizedPayloadRejection": True,
            "concurrentPollers": list(CONCURRENCY_LEVELS),
            "endpointBusyRecovery": True,
            "endpointBusyStorm": True,
            "liveRuneLite": False,
            "javaDenseScene": False,
        },
        "files": {
            "finalMetrics": "final_metrics.json",
            "concurrencySoak": "concurrency_soak.json",
            "queueBehavior": "queue_behavior.json",
        },
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "final_metrics.json": final_metrics,
        "concurrency_soak.json": concurrency,
        "queue_behavior.json": queue_behavior,
        "synthetic_soak.json": summary,
    }
    for name, value in artifacts.items():
        (output_directory / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded synthetic telemetry production-soak harness."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="canonical snapshot fixture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="artifact directory (default: timestamped telemetry proof directory)",
    )
    parser.add_argument("--samples", type=int, default=1_000)
    parser.add_argument("--concurrency-samples", type=int, default=200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 10 <= args.samples <= 20_000:
        raise SystemExit("--samples must be between 10 and 20000")
    if not 8 <= args.concurrency_samples <= 5_000:
        raise SystemExit("--concurrency-samples must be between 8 and 5000")
    output = args.output
    if output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = Path("_run_proofs/telemetry_pipeline_live") / timestamp
    summary = run_soak(
        fixture_path=args.fixture,
        output_directory=output,
        samples=args.samples,
        concurrency_samples=args.concurrency_samples,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "schema": summary["schema"],
                "output": str(output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
