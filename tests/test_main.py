from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from osrs_bot.__main__ import _observation_summary, _parser, main
from osrs_bot.configuration import MAX_ACTIONS
from osrs_bot.engine_frame import EngineFramePublisher
from osrs_bot.observation import parse_observation


_FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_loaded.json"


class MainContractTests(unittest.TestCase):
    def test_observe_summary_is_task_neutral(self) -> None:
        observation = parse_observation(
            json.loads(_FIXTURE.read_text(encoding="utf-8"))
        )

        summary = _observation_summary(observation)

        inventory = summary["inventory"]
        self.assertNotIn("ordinaryLogs", inventory)
        self.assertEqual(
            [(1351, 1), (1511, 1), (1511, 1)],
            [(item["itemId"], item["quantity"]) for item in inventory["items"]],
        )

    def test_cli_runtime_values_cannot_exceed_engine_caps(self) -> None:
        errors = StringIO()

        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["task", "--max-actions", str(MAX_ACTIONS + 1)])

        self.assertEqual(2, raised.exception.code)
        self.assertIn("max_actions", errors.getvalue())

    def test_overlay_is_explicitly_opt_in_and_rejected_detail_requires_it(self) -> None:
        parser = _parser()

        disabled = parser.parse_args(["task"])
        enabled = parser.parse_args(["task", "--overlay", "--overlay-show-rejected"])

        self.assertFalse(disabled.overlay)
        self.assertFalse(disabled.overlay_show_rejected)
        self.assertTrue(enabled.overlay)
        self.assertTrue(enabled.overlay_show_rejected)

        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["task", "--overlay-show-rejected"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("requires --overlay", errors.getvalue())

    def test_overlay_startup_failure_does_not_change_engine_result(self) -> None:
        class Runtime:
            frame_publisher = EngineFramePublisher()

            @staticmethod
            def run(*, execute: bool = False):
                class Result:
                    successful = True

                    @staticmethod
                    def to_dict():
                        return {"status": "DRY_RUN", "execute": execute}

                return Result()

        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("osrs_bot.__main__.ObservationClient"),
            patch("osrs_bot.__main__.WoodcutBankTask"),
            patch("osrs_bot.__main__.TaskRuntime", return_value=Runtime()),
            patch("osrs_bot.debug_overlay.DebugOverlay") as overlay_type,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            overlay_type.return_value.start.side_effect = RuntimeError(
                "passive styles unavailable"
            )
            exit_code = main(["task", "--overlay"])

        self.assertEqual(0, exit_code)
        self.assertEqual("DRY_RUN", json.loads(stdout.getvalue())["status"])
        self.assertIn("Diagnostic overlay unavailable", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
