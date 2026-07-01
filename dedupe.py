# dedupe.py (replace existing _parse_timestamp)
from datetime import datetime, timezone, timedelta

def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Try ISO first; this will return an aware datetime if offset present
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # Normalize to UTC and return aware datetime
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        try:
            # Fallback: parse common "YYYY-MM-DD HH:MM ET" format
            # Note: ET is ambiguous (EST/EDT). We treat "ET" as US/Eastern offset.
            # If you want strict DST-aware parsing, consider using dateutil or pytz.
            naive = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
            # Assume ET is Eastern Time; convert to UTC by adding offset.
            # Eastern is UTC-5 or UTC-4 depending on DST; here we assume DST-aware is not required,
            # so treat ET as UTC-5 (standard). If you need DST correctness, use dateutil.tz.
            eastern_offset = timedelta(hours=-5)
            aware = naive.replace(tzinfo=timezone(eastern_offset))
            return aware.astimezone(timezone.utc)
        except Exception:
            return None
