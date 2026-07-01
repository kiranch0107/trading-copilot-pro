# dedupe.py
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone
from storage import read_alerts, write_alerts, write_archive
from config import Config

def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """
    Parse timestamp string and return a timezone-aware UTC datetime.
    Uses dateutil if available; otherwise falls back to ISO parsing and a simple ET fallback.
    """
    if not ts:
        return None

    # Try dateutil if installed (best coverage)
    try:
        from dateutil import parser  # type: ignore
        parsed = parser.parse(ts)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO fallback (handles "2023-01-01T12:00:00Z" and offsets)
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # Final fallback: "YYYY-MM-DD HH:MM ET" treated as Eastern Standard Time (UTC-5)
    try:
        naive = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        eastern_offset = timedelta(hours=-5)
        aware = naive.replace(tzinfo=timezone(eastern_offset))
        return aware.astimezone(timezone.utc)
    except Exception:
        return None

def append_alert_safe(alert: Dict, cfg: Config) -> bool:
    """
    Append alert to storage only if not duplicate per cfg policy.
    Returns True if appended, False if skipped as duplicate.
    """
    cfg.validate()
    existing = read_alerts(cfg)
    now = datetime.now(timezone.utc)

    ticker = str(alert.get("ticker", "")).upper()
    entry = _safe_float(alert.get("entry"))
    target = _safe_float(alert.get("target"))
    alert_id = alert.get("id")

    # iterate from newest to oldest for quicker duplicate detection
    for a in reversed(existing):
        try:
            if str(a.get("ticker", "")).upper() != ticker:
                continue

            # Primary policy: dedupe by id
            if cfg.dedupe_by == "id":
                if a.get("id") and alert_id and a.get("id") == alert_id:
                    return False

                # fallback: entry+target within window
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                ts = _parse_timestamp(a.get("timestamp"))
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        if ts is None:
                            # treat unparseable timestamp as recent duplicate
                            return False
                        if (now - ts) < timedelta(minutes=cfg.dedupe_window_minutes):
                            return False

            # Alternate policy: dedupe by entry+target
            elif cfg.dedupe_by == "entry_target":
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                ts = _parse_timestamp(a.get("timestamp"))
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        if ts is None:
                            return False
                        if (now - ts) < timedelta(minutes=cfg.dedupe_window_minutes):
                            return False

            # final safety: exact id match
            if a.get("id") and alert_id and a.get("id") == alert_id:
                return False
        except Exception:
            # ignore malformed existing entries and continue scanning
            continue

    # Not duplicate: append and persist atomically via storage.write_alerts
    existing.append(alert)
    write_alerts(cfg, existing)
    return True

def clear_recent_alerts(cfg: Config, archive: bool = False) -> Dict[str, int]:
    """
    Remove alerts within recent window. If archive True, write removed to archive file.
    Returns counts: {"removed": n, "remaining": m}
    """
    alerts = read_alerts(cfg)
    now = datetime.now(timezone.utc)
    keep = []
    removed = []

    for a in alerts:
        ts = _parse_timestamp(a.get("timestamp"))
        if ts is None:
            # treat unparseable timestamps as recent and remove
            removed.append(a)
        elif (now - ts) <= timedelta(minutes=cfg.recent_alert_window_minutes):
            removed.append(a)
        else:
            keep.append(a)

    if archive and removed:
        write_archive(cfg, removed)

    write_alerts(cfg, keep)
    return {"removed": len(removed), "remaining": len(keep)}
