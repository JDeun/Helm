from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from route_contract_lib import infer_chosen_tool


class HelmRouteContractLibTests(unittest.TestCase):
    def test_infers_python_module_name(self) -> None:
        self.assertEqual(infer_chosen_tool(["python3", "-m", "tools.router_runner", "--help"]), "tools.router_runner")

    def test_infers_nested_shell_command_tool(self) -> None:
        self.assertEqual(
            infer_chosen_tool(["bash", "-lc", "python3 /tmp/router_runner.py --help"]),
            "router_runner.py",
        )


if __name__ == "__main__":
    unittest.main()
