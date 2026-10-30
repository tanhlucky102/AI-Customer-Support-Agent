"""Start every demo server in one terminal (Ctrl+C stops them all):

    python scripts/run_all.py

  8000  Agent Server  (widget, chat API, agent)
  8001  Demo Mobile   (level-1 shop, contract C2 API)
  8002  Sample Store  (level-0 static page, FAQ only)
  8003  Phụ Kiện Xanh (level-1 shop with its own API, mapped by commerce.json)

Run scripts/setup_demo.py once before the first start.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVERS = [
    ("agent", [sys.executable, "-m", "uvicorn", "agent_server.app:app", "--port", "8000"], ROOT),
    ("demo-shop", [sys.executable, "-m", "uvicorn", "app:app", "--port", "8001"], ROOT / "shop_demo"),
    ("sample-store", [sys.executable, "-m", "http.server", "8002"], ROOT / "examples" / "third_party_site"),
    ("second-shop", [sys.executable, "-m", "uvicorn", "examples.second_shop.app:app", "--port", "8003"], ROOT),
]


def main() -> None:
    env = {**os.environ, "PYTHONUTF8": "1"}
    processes = []
    for name, command, cwd in SERVERS:
        print(f"starting {name}: {' '.join(command[1:])}")
        processes.append(subprocess.Popen(command, cwd=cwd, env=env))
    print("\nOpen http://localhost:8001 (an@demo.vn / demo1234) or http://localhost:8003 "
          "(chau@pkx.vn / demo1234). Ctrl+C to stop.\n")
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    main()
