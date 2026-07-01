# ui.py — replace existing get_recent_alerts with this
from datetime import datetime, timezone
from typing import List, Dict
from config import Config
from storage import read_alerts
from dedupe import _parse_timestamp

def get_recent_alerts(cfg: Config) -> List[Dict]:
    alerts = read_alerts(cfg)
    now = datetime.now(timezone.utc)
    recent = []
    for a in alerts:
        ts = a.get("timestamp")
        parsed = _parse_timestamp(ts) if ts else None
        if parsed:
            # parsed is expected to be timezone-aware UTC
            if (now - parsed).total_seconds() <= cfg.recent_alert_window_minutes * 60:
                recent.append(a)
        else:
            # include unparseable timestamps as recent to avoid accidental deletion
            recent.append(a)
    return recent
