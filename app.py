# app.py
# Restored admin UI: watchlist scans, alerts table, manual simulator.
# Assumes these files are present in the same directory:
# config.py, storage.py, dedupe.py, filters.py, ui.py
import importlib
import json
import base64
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

# Local modules (top-level imports)
from config import Config
from storage import make_json_serializable, read_alerts, write_alerts
from dedupe import append_alert_safe, clear_recent_alerts
from ui import alerts_ui_panel, get_recent_alerts
from filters import candidate_passes_checks

# Try to import user's original app module (non-destructive)
USER_APP_MODULE = None
USER_APP_NAME = None
for name in ("trading_copilot_elite", "trading_copilot_pro", "trading_copilot", "app_original", "app"):
    try:
        USER_APP_MODULE = importlib.import_module(name)
        USER_APP_NAME = name
        break
    except Exception:
        USER_APP_MODULE = None

# Load config and validate
cfg = Config()
if USER_APP_MODULE:
    for field in cfg.__dataclass_fields__:
        if hasattr(USER_APP_MODULE, field.upper()):
            try:
                setattr(cfg, field, getattr(USER_APP_MODULE, field.upper()))
            except Exception:
                pass
try:
    cfg.validate()
except Exception as e:
    print("Config validation failed:", e)

# Helper: safe call into user module
def safe_call(func_name: str, *args, **kwargs):
    if USER_APP_MODULE and hasattr(USER_APP_MODULE, func_name):
        return getattr(USER_APP_MODULE, func_name)(*args, **kwargs)
    raise ImportError(f"Function '{func_name}' not found in user app module.")

# UI
st.set_page_config(page_title="Trading Copilot — Admin", layout="wide")
st.title("Trading Copilot — Admin")

st.markdown("Controls: run watchlist scans, view alerts, and simulate single alerts. Integrates with your app module when available.")

# Sidebar: recent alerts + clear (keeps your existing panel)
with st.sidebar:
    st.header("Alerts")
    alerts_ui_panel(cfg, sidebar=True)

# Main controls: Watchlist scans and Alerts snapshot
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Watchlist scans")
    st.markdown("Run a **fast** scan (subset) or a **full** scan (entire watchlist). If your module exposes `scan_watchlist`, it will be used.")

    # Determine watchlist
    try:
        watchlist = list(getattr(USER_APP_MODULE, "WATCHLIST"))
    except Exception:
        watchlist = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]

    st.write(f"Watchlist ({len(watchlist)}): {', '.join(watchlist[:10])}{'...' if len(watchlist)>10 else ''}")

    run_fast = st.button("Run fast scan (subset)")
    run_full = st.button("Run full scan (all)")

    if run_fast or run_full:
        mode = "fast" if run_fast else "full"
        st.info(f"Running {mode} scan...")
        try:
            # If user module provides scan_watchlist, call it; otherwise show stub behavior
            if USER_APP_MODULE and hasattr(USER_APP_MODULE, "scan_watchlist"):
                scan_list = watchlist[:5] if mode == "fast" else watchlist
                results = safe_call("scan_watchlist", scan_list)
                # normalize results: allow tuple (results, debug) or list
                if isinstance(results, tuple) and len(results) >= 1:
                    scan_results = results[0]
                    scan_debug = results[1] if len(results) > 1 else None
                else:
                    scan_results = results
                    scan_debug = None
            else:
                # stub: no scan_watchlist available
                scan_results = []
                scan_debug = [["stub", "NO_SIGNAL", ["scan_watchlist not implemented in user module"]]]
            st.success(f"Scan complete — found {len(scan_results) if scan_results is not None else 0} results")
            st.write("Scan debug (sample):")
            st.code(json.dumps(scan_debug or [], indent=2)[:4000])

            # If scan_results contains candidate alerts, optionally append them
            appended = 0
            skipped = 0
            if scan_results:
                for item in scan_results:
                    # Expect item to be either a dict candidate or tuple (ticker, candidate, metadata)
                    try:
                        if isinstance(item, dict) and "ticker" in item:
                            ticker = item.get("ticker")
                            candidate = item
                        elif isinstance(item, (list, tuple)) and len(item) >= 1:
                            # try to extract ticker and candidate
                            ticker = item[0] if isinstance(item[0], str) else item[0].get("ticker", "UNK")
                            candidate = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
                        else:
                            continue
                        # Build alert fields from candidate or defaults
                        current_price = float(candidate.get("underlying_price", candidate.get("price", 0) or 0))
                        entry_price = float(candidate.get("entry", candidate.get("strike", 0) or 0))
                        stop_price = float(candidate.get("stop", 0) or 0)
                        target_price = float(candidate.get("target", candidate.get("target_price", 0) or 0))
                        rr_value = candidate.get("rr", None)
                        trend_label = candidate.get("trend", "Auto")
                        strength_label = candidate.get("strength", "Auto")
                        filters_passed = candidate.get("filters_passed", {})

                        # Validate candidate via structured checks before alerting
                        debug = []
                        passes = candidate_passes_checks(candidate, cfg, debug)
                        if not passes:
                            skipped += 1
                            continue

                        # Build alert dict
                        now = datetime.utcnow().isoformat() + "Z"
                        alert_id = f"{ticker.upper()}_{int(datetime.utcnow().timestamp())}"
                        alert = {
                            "id": alert_id,
                            "timestamp": now,
                            "ticker": ticker.upper(),
                            "trend": trend_label,
                            "strength": strength_label,
                            "price": float(current_price),
                            "entry": float(entry_price),
                            "stop": float(stop_price),
                            "target": float(target_price),
                            "rr": float(rr_value) if rr_value is not None else None,
                            "filters_passed": filters_passed,
                            "journaled": False,
                            "option": candidate
                        }
                        appended_flag = append_alert_safe(alert, cfg)
                        if appended_flag:
                            appended += 1
                        else:
                            skipped += 1
                    except Exception:
                        skipped += 1
            st.write({"appended": appended, "skipped": skipped})
        except Exception as e:
            st.error(f"Scan failed: {e}")

with col2:
    st.subheader("Alerts snapshot")
    st.markdown("Current `alert_history.json` contents (recent first). You can download or clear recent alerts.")
    alerts = read_alerts(cfg)
    if not alerts:
        st.info("No alerts recorded")
    else:
        # show most recent 50
        recent = list(reversed(alerts))[:50]
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

        # download link
        b = json.dumps(make_json_serializable(alerts), indent=2).encode()
        b64 = base64.b64encode(b).decode()
        href = f'<a href="data:application/json;base64,{b64}" download="alert_history.json">Download alert_history.json</a>'
        st.markdown(href, unsafe_allow_html=True)

        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Clear recent alerts (permanent)"):
                result = clear_recent_alerts(cfg, archive=False)
                st.success(f"Permanently removed {result['removed']} alerts; {result['remaining']} remain")
        with col_b:
            if st.button("Archive and clear recent alerts"):
                result = clear_recent_alerts(cfg, archive=True)
                st.success(f"Archived and removed {result['removed']} alerts; {result['remaining']} remain")

# Manual simulator (keeps the quick test)
st.markdown("---")
st.subheader("Manual alert simulator (quick check)")
with st.form("sim"):
    t = st.text_input("Ticker", value="SPY")
    entry = st.number_input("Entry", value=0.0, format="%.2f")
    target = st.number_input("Target", value=0.0, format="%.2f")
    price = st.number_input("Price", value=0.0, format="%.2f")
    submit = st.form_submit_button("Simulate alert append")
    if submit:
        candidate = {"dte": 1, "volume": 1000, "oi": 500, "mid": 1.0, "spread": 0.01}
        # reuse same accept logic as before
        debug = []
        ok = candidate_passes_checks(candidate, cfg, debug)
        if not ok:
            st.warning("Candidate failed checks")
            st.write(debug)
        else:
            now = datetime.utcnow().isoformat() + "Z"
            alert_id = f"{t.upper()}_{int(datetime.utcnow().timestamp())}"
            alert = {
                "id": alert_id,
                "timestamp": now,
                "ticker": t.upper(),
                "trend": "Manual",
                "strength": "Manual",
                "price": float(price),
                "entry": float(entry),
                "stop": 0.0,
                "target": float(target),
                "rr": None,
                "filters_passed": {},
                "journaled": False,
                "option": candidate
            }
            appended = append_alert_safe(alert, cfg)
            if appended:
                st.success("Alert appended")
            else:
                st.info("Alert skipped (duplicate)")

st.caption("If your original app exposes `scan_watchlist`, this UI will call it. If not, implement `scan_watchlist(scan_list)` in your app module to enable automated scanning.")
