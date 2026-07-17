from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .behavior import MAX_BEHAVIOR_SEED
from .engine_frame import EngineFrame, EngineFramePublisher
from .runtime import RuntimeResult, RuntimeStatistics


LIVE_EVIDENCE_SCHEMA = "movement_targeting_live_evidence.v1"
LIVE_RECEIPT_SCHEMA = "movement_targeting_live_receipt.v1"


class LiveRunEvidenceRecorder:
    """Append-only diagnostic evidence for one existing production run.

    The recorder subscribes to immutable EngineFrames and has no task, safety,
    input, or runtime control authority.  A recorder failure is retained in the
    receipt when possible and never changes the production outcome.
    """

    def __init__(
        self,
        *,
        output_root: Path,
        run_id: str,
        started_at: datetime,
        profile_id: str,
        definition_id: str,
        configured_behavior_seed: int | None,
        behavior_seed: int,
        publisher: EngineFramePublisher,
    ) -> None:
        if not isinstance(output_root, Path):
            raise TypeError("output_root must be Path")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text")
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        if (
            isinstance(behavior_seed, bool)
            or not isinstance(behavior_seed, int)
            or not 0 <= behavior_seed <= MAX_BEHAVIOR_SEED
        ):
            raise ValueError("behavior_seed must be a valid resolved run seed")
        timestamp = started_at.astimezone(timezone.utc).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        self.directory = output_root / timestamp
        self.directory.mkdir(parents=True, exist_ok=False)
        self.frames_path = self.directory / "engine_frames.jsonl"
        self.receipt_path = self.directory / "run_receipt.json"
        self.manifest_path = self.directory / "run_manifest.json"
        self.frames_path.touch(exist_ok=False)
        self._lock = threading.Lock()
        self._run_id = run_id
        self._started_at = started_at.astimezone(timezone.utc)
        self._behavior_seed = behavior_seed
        self._frame_count = 0
        self._first_sequence: int | None = None
        self._last_sequence: int | None = None
        self._errors: list[str] = []
        self._closed = False
        self._unsubscribe: Callable[[], None] | None = None
        manifest = {
            "schema": LIVE_EVIDENCE_SCHEMA,
            "runId": run_id,
            "mode": "production",
            "startedAtUtc": self._started_at.isoformat(),
            "profileId": profile_id,
            "definitionId": definition_id,
            "configuredBehaviorSeed": configured_behavior_seed,
            "behaviorSeed": behavior_seed,
            "files": {
                "engineFrames": self.frames_path.name,
                "runReceipt": self.receipt_path.name,
            },
        }
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._unsubscribe = publisher.subscribe(self.record_frame)

    def record_frame(self, frame: EngineFrame) -> None:
        if not isinstance(frame, EngineFrame):
            return
        with self._lock:
            if self._closed:
                return
            try:
                with self.frames_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(frame.to_dict(), sort_keys=True) + "\n")
                    stream.flush()
            except Exception as error:
                self._errors.append(f"frame {frame.sequence}: {type(error).__name__}: {error}")
                return
            self._frame_count += 1
            if self._first_sequence is None:
                self._first_sequence = frame.sequence
            self._last_sequence = frame.sequence

    def finish(
        self,
        *,
        result: RuntimeResult | None,
        statistics: RuntimeStatistics,
        worker_error: str | None,
        finished_at: datetime,
        final_frame: EngineFrame | None,
    ) -> None:
        if finished_at.tzinfo is None:
            raise ValueError("finished_at must be timezone-aware")
        unsubscribe = self._unsubscribe
        if unsubscribe is not None:
            unsubscribe()
            self._unsubscribe = None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            receipt = {
                "schema": LIVE_RECEIPT_SCHEMA,
                "runId": self._run_id,
                "mode": "production",
                "startedAtUtc": self._started_at.isoformat(),
                "finishedAtUtc": finished_at.astimezone(timezone.utc).isoformat(),
                "behaviorSeed": self._behavior_seed,
                "frames": {
                    "path": self.frames_path.name,
                    "count": self._frame_count,
                    "firstSequence": self._first_sequence,
                    "lastSequence": self._last_sequence,
                },
                "result": None if result is None else result.to_dict(),
                "statistics": statistics.to_dict(),
                "workerError": worker_error,
                "finalEngineFrame": (
                    None if final_frame is None else final_frame.to_dict()
                ),
                "recorderErrors": list(self._errors),
            }
            try:
                with self.receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
                    stream.flush()
            except Exception as error:
                self._errors.append(
                    f"receipt: {type(error).__name__}: {error}"
                )
