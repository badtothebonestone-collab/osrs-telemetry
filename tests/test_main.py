from __future__ import annotations

import ast
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

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

        with redirect_stderr(errors):
            result = main(["task", "--max-actions", str(MAX_ACTIONS + 1)])

        self.assertEqual(2, result)
        self.assertIn("max_actions", errors.getvalue())

    def test_task_alias_forwards_all_arguments_to_application_cli(self) -> None:
        arguments = [
            "--execute",
            "--arduino-port",
            "COM6",
            "--overlay",
            "--overlay-show-rejected",
            "--max-actions",
            "7",
        ]
        with patch("osrs_bot.application_cli.main", return_value=7) as forwarded:
            result = main(["task", *arguments])

        self.assertEqual(7, result)
        forwarded.assert_called_once_with(["run", *arguments])

    def test_task_alias_has_no_engine_composition_authority(self) -> None:
        source = (Path(__file__).parents[1] / "osrs_bot" / "__main__.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        self.assertIn("from .application_cli import main as application_main", source)
        for forbidden in (
            "BehaviorPolicy",
            "TaskRuntime",
            "build_live_runtime",
            "WoodcutBankTask",
            "Verifier",
        ):
            self.assertNotIn(forbidden, imports)
            self.assertNotIn(f"{forbidden}(", source)


if __name__ == "__main__":
    unittest.main()
