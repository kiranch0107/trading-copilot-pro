# dedupe.py — replace existing _parse_timestamp with this
from datetime import datetime, timezone, timedelta
from typing import Optional

def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """
    Parse timestamp string and return a timezone-aware UTC datetime.
    Supports ISO formats (with or without Z) and falls back to a simple
    "YYYY-MM-DD HH:MM ET" parse treated as Eastern Standard Time (UTC-5).
    If python-dateutil is available, use it for robust parsing and timezone handling.
    """
    if not ts:
        return None

    # Try to use dateutil if available (handles many formats and timezones)
    try:
        from dateutil import parser, tz  # type: ignore
        parsed = parser.parse(ts)
        if parsed.tzinfo is None:
            # assume naive times are UTC
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # Fallback: try ISO parsing and normalize to UTC
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # Final fallback: parse "YYYY-MM-DD HH:MM ET" and treat ET as UTC-5
    try:
        naive = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        eastern_offset = timedelta(hours=-5)
        aware = naive.replace(tzinfo=timezone(eastern_offset))
        return aware.astimezone(timezone.utc)
    except Exception:
        return None
