from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from osrs_bot.engine_frame import EngineFramePublisher, EngineStage
from osrs_bot.live_evidence import LiveRunEvidenceRecorder
from osrs_bot.runtime import RuntimeResult, RuntimeStatistics
from osrs_bot.task_contract import TaskSnapshot, TaskStatus


class LiveRunEvidenceRecorderTests(unittest.TestCase):
    def test_records_every_published_frame_and_write_once_terminal_receipt(self) -> None:
        publisher = EngineFramePublisher()
        started = datetime(2026, 7, 12, 23, 1, 2, 345678, tzinfo=timezone.utc)
        task = TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree")
        result = RuntimeResult("COMPLETE", "test complete", task, 2, 0, 2)
        statistics = RuntimeStatistics(False, "COMPLETE", "test complete", 2, 0, 2)

        with tempfile.TemporaryDirectory() as directory:
            recorder = LiveRunEvidenceRecorder(
                output_root=Path(directory),
                run_id="run-000001",
                started_at=started,
                profile_id="default_lumbridge_west_trees_v1",
                definition_id="lumbridge_west_trees_v1",
                configured_behavior_seed=123,
                behavior_seed=123,
                publisher=publisher,
            )
            frames = [
                publisher.publish(stage=stage, task=task)
                for stage in (
                    EngineStage.STARTING,
                    EngineStage.OBSERVED,
                    EngineStage.DECIDED,
                )
            ]
            recorder.finish(
                result=result,
                statistics=statistics,
                worker_error=None,
                finished_at=started + timedelta(seconds=1),
                final_frame=frames[-1],
            )
            publisher.publish(stage=EngineStage.TERMINAL, task=task)

            rows = [
                json.loads(line)
                for line in recorder.frames_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([1, 2, 3], [row["sequence"] for row in rows])
            receipt = json.loads(recorder.receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(3, receipt["frames"]["count"])
            self.assertEqual("COMPLETE", receipt["result"]["status"])
            self.assertEqual("COMPLETE", receipt["statistics"]["status"])
            self.assertIn("cleanup", receipt["finalEngineFrame"])
            self.assertEqual([], receipt["recorderErrors"])
            self.assertEqual(123, receipt["behaviorSeed"])
            manifest = json.loads(recorder.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(123, manifest["configuredBehaviorSeed"])
            self.assertEqual(123, manifest["behaviorSeed"])
            self.assertEqual("production", manifest["mode"])

    def test_listener_failure_cannot_interrupt_frame_publication(self) -> None:
        publisher = EngineFramePublisher()
        task = TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree")

        def fail(_frame) -> None:
            raise RuntimeError("diagnostic failure")

        unsubscribe = publisher.subscribe(fail)
        frame = publisher.publish(stage=EngineStage.STARTING, task=task)
        unsubscribe()

        self.assertEqual(1, frame.sequence)
        self.assertIs(frame, publisher.latest())


if __name__ == "__main__":
    unittest.main()
