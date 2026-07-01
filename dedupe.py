# dedupe.py
from typing import Dict, Optional
from datetime import datetime, timedelta
# top-level imports (files must be in the same directory)
from storage import read_alerts, write_alerts, write_archive
from config import Config

def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # handle ISO with Z or offset
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        try:
            # fallback to common format used in your app
            return datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
        except Exception:
            return None

def append_alert_safe(alert: Dict, cfg: Config) -> bool:
    """
    Append alert to storage only if not duplicate per cfg policy.
    Returns True if appended, False if skipped as duplicate.
    """
    cfg.validate()
    existing = read_alerts(cfg)
    now = datetime.utcnow()

    ticker = str(alert.get("ticker", "")).upper()
    entry = _safe_float(alert.get("entry"))
    target = _safe_float(alert.get("target"))
    alert_id = alert.get("id")

    for a in reversed(existing):
        try:
            if str(a.get("ticker", "")).upper() != ticker:
                continue
            if cfg.dedupe_by == "id":
                # primary: exact id match
                if a.get("id") and alert_id and a.get("id") == alert_id:
                    return False
                # fallback: entry+target within window
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                ts = _parse_timestamp(a.get("timestamp"))
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        if ts and (now - ts) < timedelta(minutes=cfg.dedupe_window_minutes):
                            return False
                        if ts is None:
                            return False
            elif cfg.dedupe_by == "entry_target":
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                ts = _parse_timestamp(a.get("timestamp"))
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        if ts and (now - ts) < timedelta(minutes=cfg.dedupe_window_minutes):
                            return False
                        if ts is None:
                            return False
            # final fallback: exact id match
            if a.get("id") and alert_id and a.get("id") == alert_id:
                return False
        except Exception:
            continue

    # Not duplicate: append and save
    existing.append(alert)
    write_alerts(cfg, existing)
    return True

def clear_recent_alerts(cfg: Config, archive: bool = False) -> Dict[str, int]:
    alerts = read_alerts(cfg)
    now = datetime.utcnow()
    keep = []
    removed = []
    for a in alerts:
        ts = _parse_timestamp(a.get("timestamp"))
        if ts is None:
            removed.append(a)
        elif (now - ts) <= timedelta(minutes=cfg.recent_alert_window_minutes):
            removed.append(a)
        else:
            keep.append(a)

    if archive and removed:
        write_archive(cfg, removed)

    write_alerts(cfg, keep)
    return {"removed": len(removed), "remaining": len(keep)}
