"""Mask personal data before it reaches logs, traces or the language model."""

import logging
import re

# Local parts may hold Vietnamese letters ("nguyễn.van@...").
EMAIL = re.compile(r"\b([\w.%+-])[\w.%+-]*@([\w.-]+\.[A-Za-z]{2,})\b")
# A Vietnamese phone number: 0 or +84 (also "(+84)", "(091)"), then 9 digits for mobiles or 10 for
# landlines ("024 3823 4567"), optionally grouped with spaces, dots, dashes or brackets.
PHONE = re.compile(r"(?<![\d+])(?:\(?\+84\)?|84|\(?0)(?:[\s.)-]{0,2}\d){9,10}(?!\d)")


def _text(value):
    """Shops may send numbers or structured fields; masking works on text, anything else is hidden."""
    if value is None or isinstance(value, str):
        return value
    return "***" if isinstance(value, (dict, list, tuple)) else str(value)


def mask_email(value: str | None) -> str | None:
    value = _text(value)
    return EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value) if value else value


def _mask_number(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))[-9:]
    return f"0{digits[:2]}****{digits[-3:]}"


def mask_phone(value: str | None) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):  # 912345678: the leading 0 was lost
        return "***" + str(value)[-3:]
    value = _text(value)
    return PHONE.sub(_mask_number, value) if value else value


def mask_address(value: str | None) -> str | None:
    """Keep only the last two parts (district, city): enough to confirm, useless to a stranger."""
    value = _text(value)
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
        if record.exc_info and not record.exc_text:  # Tracebacks quote values too.
            record.exc_text = mask_text(logging.Formatter().formatException(record.exc_info))
        return True
