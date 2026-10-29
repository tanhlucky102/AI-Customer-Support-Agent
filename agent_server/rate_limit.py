"""In-memory chat limits for this single-process prototype; counters reset on restart."""

import math
from collections import deque
from datetime import date, datetime, timedelta
from time import monotonic


class ChatLimiter:
    """Sliding one-minute windows per customer and per IP, plus a daily quota per shop (0 disables)."""

    def __init__(self, per_customer_per_minute: int, per_ip_per_minute: int,
                 daily_per_shop: int) -> None:
        self.limits = {"customer_rate_limited": per_customer_per_minute,
                       "ip_rate_limited": per_ip_per_minute}
        self.daily_per_shop = daily_per_shop
        self.windows: dict[tuple[str, ...], deque[float]] = {}
        self.day = date.today()
        self.daily: dict[str, int] = {}

    def _window(self, key: tuple[str, ...], now: float) -> deque[float]:
        window = self.windows.setdefault(key, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        return window

    def check(self, shop_id: str, customer_id: str, ip: str) -> tuple[str, int] | None:
        """Count one chat, or return (reason, Retry-After seconds) without counting it."""
        now = monotonic()
        today = date.today()
        if today != self.day:
            self.day, self.daily = today, {}
        if len(self.windows) > 10_000:  # Forget idle senders so memory stays bounded.
            self.windows = {key: window for key, window in self.windows.items()
                            if window and now - window[-1] < 60}
        windows = {"customer_rate_limited": self._window(("customer", shop_id, customer_id), now),
                   "ip_rate_limited": self._window(("ip", ip), now)}
        for reason, window in windows.items():
            limit = self.limits[reason]
            if limit > 0 and len(window) >= limit:
                return reason, max(1, math.ceil(60 - (now - window[0])))
        if 0 < self.daily_per_shop <= self.daily.get(shop_id, 0):
            tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
            return "daily_quota_exceeded", max(1, math.ceil((tomorrow - datetime.now()).total_seconds()))
        for window in windows.values():
            window.append(now)
        self.daily[shop_id] = self.daily.get(shop_id, 0) + 1
        return None
