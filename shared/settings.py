"""Load the repository .env once; every component reads its settings from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def env_int(name: str, default: int) -> int:
    value = env(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = env(name)
    return float(value) if value else default


def env_flag(name: str, default: bool) -> bool:
    value = env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")
