from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import unittest

from osrs_bot.__main__ import _observation_summary, main
from osrs_bot.configuration import MAX_ACTIONS
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


if __name__ == "__main__":
    unittest.main()
