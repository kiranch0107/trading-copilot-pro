# app.py
# Main wrapper that uses the modularized helpers.
# Replace your existing app.py with this file (it imports the modules above).
import importlib
import json
from pathlib import Path
from datetime import datetime
import streamlit as st

# Import local modules
from config import Config
from storage import make_json_serializable
from dedupe import append_alert_safe, clear_recent_alerts
from ui import alerts_ui_panel
from filters import candidate_passes_checks

# Try to import user's original app module (non-destructive)
USER_APP_MODULE = None
USER_APP_NAME = None
for name in ("trading_copilot_elite", "trading_copilot_pro", "trading_copilot", "app_original"):
    try:
        USER_APP_MODULE = importlib.import_module(name)
        USER_APP_NAME = name
        break
    except Exception:
        USER_APP_MODULE = None

# Load config (allow override from user module globals)
cfg = Config()
if USER_APP_MODULE:
    # override config fields if present in user module
    for field in cfg.__dataclass_fields__:
        if hasattr(USER_APP_MODULE, field.upper()):
            try:
                setattr(cfg, field, getattr(USER_APP_MODULE, field.upper()))
            except Exception:
                pass
# validate
try:
    cfg.validate()
except Exception as e:
    # if invalid, fall back to defaults but log
    print("Config validation failed:", e)

# Streamlit UI
st.set_page_config(page_title="Trading Copilot — Admin", layout="wide")
st.title("Trading Copilot — Admin")

st.markdown("This app runs the admin helpers and shows recent alerts. Testing flows removed per user request.")

with st.sidebar:
    st.header("Alerts")
    alerts_ui_panel(cfg, sidebar=True)

# Example: function to accept a candidate and append alert safely
def accept_candidate_and_alert(candidate: dict, ticker: str, current_price: float,
                               entry_price: float, stop_price: float, target_price: float,
                               rr_value: float, trend_label: str, strength_label: str,
                               filters_passed: dict):
    """
    Validate candidate, build structured alert, and append safely using dedupe policy.
    Returns dict with outcome.
    """
    debug = []
    # run structured candidate checks
    ok = candidate_passes_checks(candidate, cfg, debug)
    if not ok:
        return {"accepted": False, "reason": "candidate_checks_failed", "debug": debug}

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

    appended = append_alert_safe(alert, cfg)
    if appended:
        return {"accepted": True, "appended": True, "alert": alert, "debug": debug}
    return {"accepted": True, "appended": False, "reason": "duplicate", "debug": debug}

st.markdown("Use the functions in this module to accept candidates and append alerts safely. The UI shows recent alerts and allows permanent clearing.")

# Minimal example controls for manual testing (non-automated)
st.subheader("Manual alert simulator (for quick checks)")
with st.form("sim"):
    t = st.text_input("Ticker", value="SPY")
    entry = st.number_input("Entry", value=0.0, format="%.2f")
    target = st.number_input("Target", value=0.0, format="%.2f")
    price = st.number_input("Price", value=0.0, format="%.2f")
    submit = st.form_submit_button("Simulate alert append")
    if submit:
        candidate = {"dte": 1, "volume": 1000, "oi": 500, "mid": 1.0, "spread": 0.01}
        outcome = accept_candidate_and_alert(candidate, t, price, entry, 0.0, target, 2.0, "Bullish", "Strong", {"adx": True})
        st.write(outcome)

st.caption("This wrapper focuses on alert storage, dedupe, and structured checks. Integrate these helpers into your signal pipeline where alerts are created.")
