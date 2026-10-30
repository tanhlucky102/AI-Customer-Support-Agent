"""Mask personal data before it reaches logs, traces or the language model."""

import logging
import re

EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# A Vietnamese phone number: 0 or +84, then 9 digits, optionally grouped ("0912 345 678").
PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)(?:[\s.-]?\d){9}(?!\d)")


def mask_email(value: str | None) -> str | None:
    return EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value) if value else value


def _mask_number(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))[-9:]
    return f"0{digits[:2]}****{digits[-3:]}"


def mask_phone(value: str | None) -> str | None:
    return PHONE.sub(_mask_number, value) if value else value


def mask_address(value: str | None) -> str | None:
    """Keep only the last two parts (district, city): enough to confirm, useless to a stranger."""
    if not value:
        return value
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return ", ".join(["***", *parts[-2:]]) if len(parts) > 2 else "***"


def mask_text(value: str) -> str:
    return mask_phone(mask_email(value))


class PiiFilter(logging.Filter):
    """Attach to log handlers so phone numbers and e-mails never reach log files."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.msg = record.getMessage()
            record.args = None
        if isinstance(record.msg, str):
            record.msg = mask_text(record.msg)
        return True
