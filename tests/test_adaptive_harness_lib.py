from __future__ import annotations

import unittest

from scripts.adaptive_harness_lib import build_hydration_commands


class AdaptiveHarnessLibTests(unittest.TestCase):
    def test_build_hydration_commands_omits_empty_include_flags(self) -> None:
        commands = build_hydration_commands(
            {
                "context": {
                    "required": True,
                    "query": "router",
                    "include": [],
                    "limit": 4,
                    "failed_include": [],
                }
            }
        )

        self.assertEqual(len(commands), 1)
        self.assertNotIn("--include", commands[0])
        self.assertEqual(commands[0][-2:], ["--limit", "4"])


if __name__ == "__main__":
    unittest.main()
