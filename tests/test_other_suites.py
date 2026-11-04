"""The demo website's own tests and the widget's JavaScript tests, so one command runs everything:
    python -m unittest discover -s tests -t .
(Each runs in its own process: shop_demo is a flat module set run from its folder, the widget in Node.)"""

import os
import shutil
import subprocess
import sys
import unittest

from tests import support


class OtherSuitesTest(unittest.TestCase):
    def run_suite(self, command: list[str], cwd) -> None:
        env = {**os.environ, "PYTHONUTF8": "1"}
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])

    def test_shop_demo(self):
        self.run_suite([sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                       support.ROOT / "shop_demo")

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_widget_javascript(self):
        self.run_suite(["node", "--test", "tests/js/*.test.js"], support.ROOT)  # Node 21+ expands the glob


if __name__ == "__main__":
    unittest.main()
