# ui.py
import streamlit as st
from typing import List, Dict
from datetime import datetime, timezone
from config import Config
from storage import read_alerts
from dedupe import _parse_timestamp, clear_recent_alerts

def alerts_ui_panel(cfg: Config, sidebar: bool = True):
    """
    Streamlit panel showing recent alerts and clear/archive controls.
    """
    container = st.sidebar if sidebar else st
    with container.expander("Recent alerts", expanded=True):
        days = cfg.recent_alert_window_minutes // (24 * 60)
        st.write(f"Showing alerts from the last **{days} days**")
        recent = get_recent_alerts(cfg)
        if not recent:
            st.info("No recent alerts")
            return

        rows = []
        for a in recent:
            rows.append({
                "id": a.get("id"),
                "ticker": a.get("ticker"),
                "price": a.get("price"),
                "entry": a.get("entry"),
                "target": a.get("target"),
                "timestamp": a.get("timestamp")
            })
        st.table(rows)

        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Clear recent alerts (permanent)"):
                result = clear_recent_alerts(cfg, archive=False)
                st.success(f"Permanently removed {result['removed']} alerts; {result['remaining']} remain")
        with col2:
            if st.button("Archive and clear recent alerts"):
                result = clear_recent_alerts(cfg, archive=True)
                st.success(f"Archived and removed {result['removed']} alerts; {result['remaining']} remain")

def get_recent_alerts(cfg: Config) -> List[Dict]:
    """
    Return alerts considered 'recent' per cfg.recent_alert_window_minutes.
    Uses timezone-aware UTC comparisons.
    """
    alerts = read_alerts(cfg)
    now = datetime.now(timezone.utc)
    recent = []
    for a in alerts:
        ts = a.get("timestamp")
        parsed = _parse_timestamp(ts) if ts else None
        if parsed:
            try:
                if (now - parsed).total_seconds() <= cfg.recent_alert_window_minutes * 60:
                    recent.append(a)
            except Exception:
                # if parsed is malformed, include to be safe
                recent.append(a)
        else:
            # include unparseable timestamps as recent to avoid accidental deletion
            recent.append(a)
    return recent
