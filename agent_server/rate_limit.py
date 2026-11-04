"""Chat limits: one-minute windows per customer and per IP, and daily quotas per shop, customer and IP.

DbChatLimiter (the server's default) counts in PostgreSQL, so several server processes share the
limits and a restart resets nothing. ChatLimiter keeps the same counters in memory (one process,
tests). A guest's ID is chosen by the browser, so guests are counted by IP in both.
"""

import math
from collections import deque
from collections.abc import Callable
from datetime import date, datetime, timedelta
from time import monotonic


class ChatLimiter:
    """Limits of 0 are disabled. A guest's ID is chosen by the browser, so guests are counted by IP."""

    def __init__(self, per_customer_per_minute: int, per_ip_per_minute: int, daily_per_shop: int,
                 daily_per_customer: int = 0, daily_per_ip: int = 0,
                 daily_used: Callable[[str], int] | None = None) -> None:
        self.limits = {"customer_rate_limited": per_customer_per_minute,
                       "ip_rate_limited": per_ip_per_minute}
        self.daily_limits = {"daily_quota_exceeded": daily_per_shop,
                             "customer_daily_limit": daily_per_customer,
                             "ip_daily_limit": daily_per_ip}
        self.daily_used = daily_used
        self.windows: dict[tuple[str, ...], deque[float]] = {}
        self.day = date.today()
        self.daily: dict[tuple[str, ...], int] = {}

    def _window(self, key: tuple[str, ...], now: float) -> deque[float]:
        window = self.windows.setdefault(key, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        return window

    def _shop_used(self, shop_id: str) -> int:
        key = ("shop", shop_id)
        if key not in self.daily:
            try:  # After a restart, continue from what was already answered today.
                self.daily[key] = self.daily_used(shop_id) if self.daily_used else 0
            except Exception:
                self.daily[key] = 0
        return self.daily[key]

    def check(self, shop_id: str, customer_id: str, ip: str, guest: bool = False) -> tuple[str, int] | None:
        """Count one chat, or return (reason, Retry-After seconds) without counting it."""
        now = monotonic()
        today = date.today()
        if today != self.day:
            self.day, self.daily = today, {}
        if len(self.windows) > 10_000:  # Forget idle senders so memory stays bounded.
            self.windows = {key: window for key, window in self.windows.items()
                            if window and now - window[-1] < 60}
        sender = ("guest", shop_id, ip) if guest else ("customer", shop_id, customer_id)
        windows = {"customer_rate_limited": self._window(sender, now),
                   "ip_rate_limited": self._window(("ip", ip), now)}
        for reason, window in windows.items():
            limit = self.limits[reason]
            if limit > 0 and len(window) >= limit:
                return reason, max(1, math.ceil(60 - (now - window[0])))
        self._shop_used(shop_id)
        counters = {"daily_quota_exceeded": ("shop", shop_id), "customer_daily_limit": sender,
                    "ip_daily_limit": ("ip-day", ip)}
        for reason, key in counters.items():
            limit = self.daily_limits[reason]
            if 0 < limit <= self.daily.get(key, 0):
                tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
                return reason, max(1, math.ceil((tomorrow - datetime.now()).total_seconds()))
        for window in windows.values():
            window.append(now)
        for key in counters.values():
            self.daily[key] = self.daily.get(key, 0) + 1
        if len(self.daily) > 50_000:  # Bounded: keep the shops' counts, drop per-sender ones.
            self.daily = {key: value for key, value in self.daily.items() if key[0] == "shop"}
        return None


class DbChatLimiter:
    """The limits of ChatLimiter, counted in the rate_events table."""

    def __init__(self, per_customer_per_minute: int, per_ip_per_minute: int, daily_per_shop: int,
                 daily_per_customer: int = 0, daily_per_ip: int = 0) -> None:
        self.minute = {"customer_rate_limited": per_customer_per_minute, "ip_rate_limited": per_ip_per_minute}
        self.daily = {"daily_quota_exceeded": daily_per_shop, "customer_daily_limit": daily_per_customer,
                      "ip_daily_limit": daily_per_ip}

    def check(self, shop_id: str, customer_id: str, ip: str, guest: bool = False) -> tuple[str, int] | None:
        from agent_server import store
        sender = f"guest:{shop_id}:{ip}" if guest else f"customer:{shop_id}:{customer_id}"
        keys = {"customer_rate_limited": sender, "ip_rate_limited": f"ip:{ip}",
                "daily_quota_exceeded": f"shop:{shop_id}", "customer_daily_limit": sender,
                "ip_daily_limit": f"ip:{ip}"}
        midnight = datetime.combine(date.today(), datetime.min.time()).astimezone()
        counts = store.rate_counts(sorted(set(keys.values())), midnight)
        empty = {"minute": 0, "age": 0.0, "day": 0}
        for reason, limit in self.minute.items():
            seen = counts.get(keys[reason], empty)
            if limit > 0 and seen["minute"] >= limit:
                return reason, max(1, math.ceil(60 - seen["age"]))
        for reason, limit in self.daily.items():
            if 0 < limit <= counts.get(keys[reason], empty)["day"]:
                tomorrow = midnight + timedelta(days=1)
                return reason, max(1, math.ceil((tomorrow - datetime.now().astimezone()).total_seconds()))
        store.rate_record(sorted(set(keys.values())))
        return None
