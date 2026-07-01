# dedupe.py — replace existing _parse_timestamp with this
from datetime import datetime, timezone, timedelta
from typing import Optional

def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Try ISO first; handle trailing Z as UTC
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # If parsed has no tzinfo, treat it as UTC
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        try:
            # Fallback: parse "YYYY-MM-DD HH:MM ET"
            # Treat "ET" as US Eastern. This fallback assumes standard time (UTC-5).
            # For DST-aware parsing, use dateutil.tz (see note below).
            naive = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
            eastern_offset = timedelta(hours=-5)
            aware = naive.replace(tzinfo=timezone(eastern_offset))
            return aware.astimezone(timezone.utc)
        except Exception:
            return None
