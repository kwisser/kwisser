"""Pure formatting and timing helpers.

All functions in this module are stateless – they receive plain values and
return plain values.  No GitHub API calls, no file I/O, no global state.
"""

import datetime
import time
from collections.abc import Callable
from typing import Any

from dateutil import relativedelta


def format_age(created_at: datetime.datetime | str) -> str:
    """Convert the account creation date into a human-readable uptime string."""
    if isinstance(created_at, str):
        created_at = datetime.datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
        created_at = created_at.replace(tzinfo=None)
    diff = relativedelta.relativedelta(datetime.datetime.today(), created_at)
    parts = [
        f"{diff.years} year{format_plural(diff.years)}",
        f"{diff.months} month{format_plural(diff.months)}",
        f"{diff.days} day{format_plural(diff.days)}",
    ]
    suffix = " on GitHub" if diff.months == 0 and diff.days == 0 else ""
    return ", ".join(parts) + suffix


def format_plural(value: int) -> str:
    """Return the plural suffix 's' when *value* is not 1."""
    return "s" if value != 1 else ""


def format_github_datetime(value: datetime.datetime) -> str:
    """Format a datetime exactly as GitHub's GraphQL DateTime scalar expects."""
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_display_text(value: int | str) -> str:
    """Normalize a value to the text form shown inside the SVG card."""
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def format_compact_number(value: int | str) -> str:
    """Shorten large numeric values so the SVG does not overflow its bounds."""
    if isinstance(value, str):
        normalized = value.replace(",", "").strip().upper()
        if normalized.endswith("M") or normalized.endswith("K"):
            return value
        value = int(normalized)

    absolute_value = abs(value)
    if absolute_value >= 1_000_000:
        formatted = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{formatted}M"
    if absolute_value >= 1_000:
        formatted = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}K"
    return str(value)


def perf_counter(function: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    """Run *function* with *args* and return ``(result, elapsed_seconds)``."""
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


def print_duration(label: str, duration: float) -> None:
    """Print one timing line in a compact human-readable format."""
    metric = f"{duration:.4f} s" if duration > 1 else f"{duration * 1000:.4f} ms"
    print(f"   {label + ':':<20}{metric:>12}")
