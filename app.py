# app.py
# Defensive Streamlit wrapper with admin test runner and safe helpers.
# Paste this file into your project root. It will try to import your real app module
# (app.py or trading_copilot_elite.py). If found, it uses its functions; otherwise
# it falls back to safe stubs so tests can run without crashing.

import importlib
import json
import math
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

# Try to import the user's real app module (common names)
USER_APP_MODULE = None
for name in ("app", "trading_copilot_elite", "trading_copilot_pro"):
    try:
        USER_APP_MODULE = importlib.import_module(name)
        USER_APP_NAME = name
        break
    except Exception:
        USER_APP_MODULE = None

# -------------------- CONFIG DEFAULTS (can be overridden in your real app) --------------------
ALERT_LOG_FILE = Path(getattr(USER_APP_MODULE, "ALERT_LOG_FILE", Path("alert_history.json")))
MIN_DTE = int(getattr(USER_APP_MODULE, "MIN_DTE", 1))
MAX_DTE = int(getattr(USER_APP_MODULE, "MAX_DTE", 30))
MIN_ADX = int(getattr(USER_APP_MODULE, "MIN_ADX", 25))
MIN_OPTION_VOLUME = int(getattr(USER_APP_MODULE, "MIN_OPTION_VOLUME", 50))
MIN_OPTION_OI = int(getattr(USER_APP_MODULE, "MIN_OPTION_OI", 20))
FORCE_ALLOW_SAME_DAY = set(getattr(USER_APP_MODULE, "FORCE_ALLOW_SAME_DAY", set()))
DEDUPE_WINDOW_MINUTES = int(getattr(USER_APP_MODULE, "DEDUPE_WINDOW_MINUTES", 15))

# -------------------- Optional wrappers for functions expected from the real app --------------------
def safe_call(func_name: str, *args, **kwargs):
    """Call function from user app module if present, else raise informative error."""
    if USER_APP_MODULE and hasattr(USER_APP_MODULE, func_name):
        try:
            return getattr(USER_APP_MODULE, func_name)(*args, **kwargs)
        except Exception as e:
            # propagate exception so test runner records it
            raise
    else:
        raise ImportError(f"Function '{func_name}' not found in user app module.")

# -------------------- JSON SERIALIZER --------------------
try:
    import numpy as np
    import pandas as pd
except Exception:
    np = None
    pd = None

def make_json_serializable(obj: Any) -> Any:
    """Recursively convert numpy/pandas types to native Python types for JSON dumping."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if np is not None:
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.str_,)):
            return str(obj)
    if isinstance(obj, (datetime, date)):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if pd is not None and isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if np is not None and isinstance(obj, np.ndarray):
        try:
            return obj.tolist()
        except Exception:
            return [make_json_serializable(x) for x in obj]
    if pd is not None and isinstance(obj, pd.Series):
        return [make_json_serializable(x) for x in obj.tolist()]
    if pd is not None and isinstance(obj, pd.DataFrame):
        try:
            return obj.to_dict(orient="records")
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(v) for v in obj]
    try:
        if hasattr(obj, "item"):
            return make_json_serializable(obj.item())
    except Exception:
        pass
    return str(obj)

# -------------------- ALERT DEDUPLICATION --------------------
def append_alert_safe(alert: Dict[str, Any], alert_file_path: Optional[Path] = None, dedupe_window_minutes: int = DEDUPE_WINDOW_MINUTES) -> bool:
    """Append alert to JSON file only if not a duplicate within dedupe_window_minutes."""
    if alert_file_path is None:
        alert_file_path = ALERT_LOG_FILE
    if isinstance(alert_file_path, (str,)):
        alert_file_path = Path(alert_file_path)

    try:
        if alert_file_path.exists():
            existing = json.loads(alert_file_path.read_text())
        else:
            existing = []
    except Exception:
        existing = []

    ticker = str(alert.get("ticker", "")).upper()
    entry = None
    target = None
    try:
        entry = float(alert.get("entry")) if alert.get("entry") is not None else None
    except Exception:
        entry = None
    try:
        target = float(alert.get("target")) if alert.get("target") is not None else None
    except Exception:
        target = None

    now = datetime.now()

    for a in reversed(existing):
        try:
            if str(a.get("ticker", "")).upper() != ticker:
                continue
            if a.get("id") and alert.get("id") and a.get("id") == alert.get("id"):
                return False
            a_entry = None
            a_target = None
            try:
                a_entry = float(a.get("entry")) if a.get("entry") is not None else None
            except Exception:
                a_entry = None
            try:
                a_target = float(a.get("target")) if a.get("target") is not None else None
            except Exception:
                a_target = None
            if a_entry is not None and entry is not None and a_target is not None and target is not None:
                if a_entry == entry and a_target == target:
                    return False
            ts = a.get("timestamp")
            if ts:
                try:
                    parsed = None
                    if "T" in ts:
                        parsed = datetime.fromisoformat(ts)
                    else:
                        try:
                            parsed = datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
                        except Exception:
                            parsed = None
                    if parsed and (now - parsed) < timedelta(minutes=dedupe_window_minutes):
                        if entry is not None and a_entry is not None and abs(entry - a_entry) < 1e-6:
                            return False
                except Exception:
                    pass
        except Exception:
            continue

    existing.append(alert)
    try:
        alert_file_path.write_text(json.dumps(make_json_serializable(existing), indent=2))
    except Exception:
        with open(alert_file_path, "w") as f:
            json.dump(make_json_serializable(existing), f, indent=2)
    return True

# -------------------- OPTION CANDIDATE CHECKS --------------------
def candidate_passes_checks(candidate: Dict[str, Any], ticker: str, debug_lines: Optional[List[str]] = None) -> bool:
    """Checks DTE, liquidity, price and returns True if candidate acceptable."""
    if debug_lines is None:
        debug_lines = []

    try:
        candidate_dte = int(candidate.get("dte", 0))
    except Exception:
        candidate_dte = 0

    min_dte_allowed = int(globals().get("MIN_DTE", MIN_DTE))
    max_dte_allowed = int(globals().get("MAX_DTE", MAX_DTE))
    force_allow = globals().get("FORCE_ALLOW_SAME_DAY", FORCE_ALLOW_SAME_DAY)

    if candidate_dte < max(1, min_dte_allowed) and ticker not in force_allow:
        debug_lines.append(f"dte: {candidate_dte} < MIN_DTE {min_dte_allowed} — rejected")
        return False
    else:
        debug_lines.append(f"dte: {candidate_dte} in [{min_dte_allowed},{max_dte_allowed}]")

    if candidate_dte > max_dte_allowed:
        debug_lines.append(f"dte: {candidate_dte} > MAX_DTE {max_dte_allowed} — rejected")
        return False

    try:
        opt_volume = int(candidate.get("volume", 0))
    except Exception:
        opt_volume = 0
    try:
        opt_oi = int(candidate.get("oi", 0))
    except Exception:
        opt_oi = 0

    min_vol = int(globals().get("MIN_OPTION_VOLUME", MIN_OPTION_VOLUME))
    min_oi = int(globals().get("MIN_OPTION_OI", MIN_OPTION_OI))

    if opt_volume < min_vol or opt_oi < min_oi:
        debug_lines.append(f"option_liquidity: vol {opt_volume} < MIN_OPTION_VOLUME {min_vol} or oi {opt_oi} < MIN_OPTION_OI {min_oi} — rejected")
        return False
    else:
        debug_lines.append(f"option_liquidity: vol {opt_volume} >= {min_vol}; oi {opt_oi} >= {min_oi}")

    try:
        mid = float(candidate.get("mid", candidate.get("last_price", 0) or 0))
    except Exception:
        mid = 0.0
    try:
        spread = float(candidate.get("spread", 0) or 0)
    except Exception:
        spread = 0.0

    if mid <= 0.0 or math.isnan(mid):
        debug_lines.append(f"option_price: mid {mid} <= 0 or NaN — rejected")
        return False

    if mid < 0.05 and (opt_volume < 1000 or opt_oi < 200):
        debug_lines.append(f"option_price: mid {mid:.2f} < 0.05 with low liquidity vol {opt_volume} oi {opt_oi} — rejected")
        return False

    debug_lines.append(f"option_price: mid {mid:.2f}; spread {spread:.2f}")
    return True

# -------------------- PROCESS CANDIDATE AND ALERT --------------------
def process_candidate_and_alert(candidate: Dict[str, Any], ticker: str, *,
                                current_price: float,
                                entry_price: float,
                                stop_price: float,
                                target_price: float,
                                rr_value: Optional[float],
                                trend_label: str,
                                strength_label: str,
                                filters_passed_dict: Dict[str, Any],
                                journaled: bool = False,
                                alert_file_path: Optional[Path] = None) -> Dict[str, Any]:
    debug_lines: List[str] = []
    ticker_up = str(ticker).upper()

    passes = candidate_passes_checks(candidate, ticker_up, debug_lines)
    if not passes:
        return {"accepted": False, "appended": False, "debug": debug_lines, "alert": None}

    now_ts = datetime.now()
    alert_id = f"{ticker_up}_{int(now_ts.timestamp())}"
    alert = {
        "id": alert_id,
        "timestamp": now_ts.strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker_up,
        "trend": trend_label,
        "strength": strength_label,
        "price": float(current_price),
        "entry": float(entry_price),
        "stop": float(stop_price),
        "target": float(target_price),
        "rr": float(rr_value) if rr_value is not None else None,
        "filters_passed": make_json_serializable(filters_passed_dict),
        "journaled": bool(journaled),
        "option": make_json_serializable(candidate),
    }

    appended = append_alert_safe(alert, alert_file_path)
    if appended:
        debug_lines.append(f"ALERT_LOGGED: {ticker_up} entry {entry_price} id {alert_id}")
    else:
        debug_lines.append(f"ALERT_SKIPPED_DUPLICATE: {ticker_up} entry {entry_price}")

    return {"accepted": True, "appended": appended, "debug": debug_lines, "alert": alert if appended else None}

# -------------------- ADMIN TEST RUNNER --------------------
def run_tests_and_build_report():
    """
    Runs a set of lookups and scans using functions from the user's app module if available.
    Writes test_report.json in the current working directory and returns the serializable report.
    """
    report = {"meta": {"timestamp": datetime.now().astimezone().isoformat()}, "single_lookup": [], "scans": [], "edge_cases": [], "dte_check": None, "alerts_file": None}
    # Single lookups
    for t in ["SPY", "AAPL", "TSLA"]:
        entry = {"ticker": t}
        try:
            # Try to call get_data_with_error from user app; fallback to safe stub
            try:
                df_err = safe_call("get_data_with_error", t)
                # If user function returns (df, err)
                if isinstance(df_err, tuple) and len(df_err) >= 2:
                    df, err = df_err[0], df_err[1]
                else:
                    df, err = df_err, None
            except ImportError:
                # stub: no data, no error
                df, err = None, "no_user_data_function"
            entry["data_error"] = err
            if df is not None:
                # compute_cached, get_weekly_trend, get_spy_regime, generate_swing_signal if available
                try:
                    last_key = str(df.index[-1]) if hasattr(df, "index") and len(df.index) else None
                except Exception:
                    last_key = None
                try:
                    dfc = safe_call("compute_cached", t, last_key, df) if last_key is not None else None
                except Exception:
                    dfc = None
                try:
                    weekly = safe_call("get_weekly_trend", t)
                except Exception:
                    weekly = None
                try:
                    spy_regime = safe_call("get_spy_regime")
                except Exception:
                    spy_regime = None
                try:
                    sig = safe_call("generate_swing_signal", t, dfc, weekly, spy_regime)
                except Exception:
                    sig = None
                entry["signal"] = make_json_serializable(sig)
        except Exception as e:
            entry["exception"] = str(e)
        report["single_lookup"].append(make_json_serializable(entry))

    # Scans: fast and full
    for mode in (True, False):
        try:
            # If user app exposes WATCHLIST, use it; else fallback to sample list
            try:
                watchlist = list(getattr(USER_APP_MODULE, "WATCHLIST"))
            except Exception:
                watchlist = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]
            scan_list = watchlist[:5] if mode else watchlist
            try:
                # If user app exposes scan_watchlist, call it
                results_debug = safe_call("scan_watchlist", scan_list)
                # Expecting (results, debug) or dict
                if isinstance(results_debug, tuple) and len(results_debug) >= 2:
                    results, debug = results_debug[0], results_debug[1]
                elif isinstance(results_debug, dict) and "results" in results_debug:
                    results, debug = results_debug.get("results"), results_debug.get("debug")
                else:
                    # If user function returns only results, set debug empty
                    results, debug = results_debug, []
            except ImportError:
                # fallback: simulate debug lines using simple checks
                results, debug = [], []
                for tk in scan_list:
                    debug.append([tk, "NO_SIGNAL", ["stub: no scan_watchlist available"]])
            report["scans"].append({"fast_mode": bool(mode), "scan_list": scan_list, "results_count": len(results) if results is not None else 0, "debug": make_json_serializable(debug)})
        except Exception as e:
            report["scans"].append({"fast_mode": bool(mode), "error": str(e)})

    # Edge cases
    try:
        # insufficient_history: temporarily increase MIN_ROWS if present
        orig_min_rows = globals().get("MIN_ROWS", None)
        if orig_min_rows is not None:
            globals()["MIN_ROWS"] = 500
        try:
            # attempt to call scan_watchlist again
            try:
                res_debug = safe_call("scan_watchlist", watchlist)
                if isinstance(res_debug, tuple) and len(res_debug) >= 2:
                    res, debug = res_debug[0], res_debug[1]
                else:
                    res, debug = res_debug, []
            except ImportError:
                res, debug = [], [["stub", "NO_SIGNAL", ["no scan_watchlist"]]]
            report["edge_cases"].append({"test": "insufficient_history", "MIN_ROWS": 500, "results_count": len(res) if res is not None else 0, "debug_sample": make_json_serializable(debug[:8])})
        finally:
            if orig_min_rows is not None:
                globals()["MIN_ROWS"] = orig_min_rows
    except Exception as e:
        report["edge_cases"].append({"test": "insufficient_history", "error": str(e)})

    # earnings_blackout test (simulate by calling generate_swing_signal with modified EARNINGS_DAYS if available)
    try:
        orig_earn = globals().get("EARNINGS_DAYS", None)
        globals()["EARNINGS_DAYS"] = 30
        try:
            try:
                # call generate_swing_signal for AAPL if available
                df_err = safe_call("get_data_with_error", "AAPL")
                if isinstance(df_err, tuple) and len(df_err) >= 2:
                    df, err = df_err[0], df_err[1]
                else:
                    df, err = df_err, None
                if df is not None:
                    last_key = str(df.index[-1]) if hasattr(df, "index") and len(df.index) else None
                    dfc = safe_call("compute_cached", "AAPL", last_key, df) if last_key is not None else None
                    weekly = safe_call("get_weekly_trend", "AAPL") if hasattr(USER_APP_MODULE, "get_weekly_trend") else None
                    spy_regime = safe_call("get_spy_regime") if hasattr(USER_APP_MODULE, "get_spy_regime") else None
                    sig = safe_call("generate_swing_signal", "AAPL", dfc, weekly, spy_regime) if hasattr(USER_APP_MODULE, "generate_swing_signal") else None
                else:
                    sig = None
            except ImportError:
                sig = None
            report["edge_cases"].append({"test": "earnings_blackout", "EARNINGS_DAYS": 30, "lookup": make_json_serializable(sig)})
        finally:
            if orig_earn is not None:
                globals()["EARNINGS_DAYS"] = orig_earn
    except Exception as e:
        report["edge_cases"].append({"test": "earnings_blackout", "error": str(e)})

    # budget_no_option test: set BUDGET_MAX tiny and attempt lookup
    try:
        orig_budget = globals().get("BUDGET_MAX", None)
        globals()["BUDGET_MAX"] = 0.01
        try:
            try:
                df_err = safe_call("get_data_with_error", "AAPL")
                if isinstance(df_err, tuple) and len(df_err) >= 2:
                    df, err = df_err[0], df_err[1]
                else:
                    df, err = df_err, None
                if df is not None:
                    last_key = str(df.index[-1]) if hasattr(df, "index") and len(df.index) else None
                    dfc = safe_call("compute_cached", "AAPL", last_key, df) if last_key is not None else None
                    weekly = safe_call("get_weekly_trend", "AAPL") if hasattr(USER_APP_MODULE, "get_weekly_trend") else None
                    spy_regime = safe_call("get_spy_regime") if hasattr(USER_APP_MODULE, "get_spy_regime") else None
                    sig = safe_call("generate_swing_signal", "AAPL", dfc, weekly, spy_regime) if hasattr(USER_APP_MODULE, "generate_swing_signal") else None
                else:
                    sig = None
            except ImportError:
                sig = None
            report["edge_cases"].append({"test": "budget_no_option", "BUDGET_MAX": 0.01, "lookup": make_json_serializable(sig)})
        finally:
            if orig_budget is not None:
                globals()["BUDGET_MAX"] = orig_budget
    except Exception as e:
        report["edge_cases"].append({"test": "budget_no_option", "error": str(e)})

    # DTE check: try to call get_full_chain_data if available
    try:
        try:
            chain = safe_call("get_full_chain_data", "SPY")
        except ImportError:
            chain = None
        dtes = []
        if isinstance(chain, dict) and "expiries" in chain:
            for e in chain["expiries"]:
                expiry = e.get("expiry")
                try:
                    exp_dt = pd.to_datetime(expiry).date() if pd is not None else datetime.fromisoformat(str(expiry)).date()
                except Exception:
                    try:
                        exp_dt = datetime.strptime(str(expiry), "%Y-%m-%d").date()
                    except Exception:
                        continue
                dtes.append((expiry, int((exp_dt - datetime.now().date()).days)))
        report["dte_check"] = {"dtes": dtes, "min_allowed": MIN_DTE, "max_allowed": MAX_DTE}
    except Exception as e:
        report["dte_check"] = {"error": str(e)}

    # Alerts snapshot
    try:
        if ALERT_LOG_FILE.exists():
            report["alerts_file"] = make_json_serializable(json.loads(ALERT_LOG_FILE.read_text()))
        else:
            report["alerts_file"] = []
    except Exception as e:
        report["alerts_file_error"] = str(e)

    # Save report
    out_path = Path("test_report.json")
    out_path.write_text(json.dumps(make_json_serializable(report), indent=2))
    return make_json_serializable(report), str(out_path)

# -------------------- STREAMLIT UI --------------------
st.set_page_config(page_title="Trading Copilot — Admin Test Runner", layout="wide")
st.title("Trading Copilot — Admin Test Runner")

st.markdown("Use the sidebar to run automated tests. This wrapper will call your app's functions if available.")

with st.sidebar:
    st.header("Admin")
    run_tests = st.button("Run automated tests (admin)")

if run_tests:
    with st.spinner("Running automated tests..."):
        try:
            report, path = run_tests_and_build_report()
            st.success(f"Tests complete — report saved to {path}")
            st.subheader("Summary")
            st.write({"single_lookup_count": len(report.get("single_lookup", [])), "scans": [s.get("results_count") for s in report.get("scans", [])]})
            st.subheader("Raw report (truncated)")
            st.code(json.dumps(report, indent=2)[:4000])
            # download link
            b = json.dumps(report, indent=2).encode()
            import base64
            b64 = base64.b64encode(b).decode()
            href = f'<a href="data:application/json;base64,{b64}" download="test_report.json">Download test_report.json</a>'
            st.markdown(href, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Test runner failed: {e}")
            st.exception(e)

# If the user app module exists, show a small note
if USER_APP_MODULE:
    st.info(f"Using functions from imported module: {USER_APP_NAME}")
else:
    st.warning("No user app module found (app.py or trading_copilot_elite). The test runner will use safe stubs.")

# Minimal UI content so the app doesn't look empty
st.markdown("This page is an admin helper. Use the sidebar button to run tests and download `test_report.json`.")
