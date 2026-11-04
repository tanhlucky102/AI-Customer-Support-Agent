"""Missing secrets must never fall back to a key that is published in the repository."""

import subprocess
import sys
import unittest
from pathlib import Path


class MissingSecretsTest(unittest.TestCase):
    def test_missing_api_key_and_token_secret_are_random_per_process(self):
        code = ("import os; os.environ['DEMO_SHOP_API_KEY'] = os.environ['DEMO_SHOP_TOKEN_SECRET'] = ''; "
                "import config; print(config.API_KEY, config.TOKEN_SECRET, len(config.MISSING_KEYS))")
        runs = [subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                               capture_output=True, text=True, check=True).stdout.split()
                for _ in range(2)]
        self.assertNotEqual(runs[0][:2], runs[1][:2])
        self.assertNotIn("dev-only", " ".join(runs[0]))
        self.assertEqual(runs[0][2], "2")


if __name__ == "__main__":
    unittest.main()
