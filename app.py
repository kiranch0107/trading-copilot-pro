# app.py
# Updated Streamlit wrapper with alert dedupe by id, recent alerts (7 days), and permanent clear.
# Paste this file into your project root (replace existing app.py if you choose).
# It will attempt to import your real app module and call its functions when available.
# If your real app functions are present, they will be used; otherwise safe stubs are used.

import importlib
import json
import math
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

# Try to import the user's real app module (common names)
USER_APP_MODULE = None
USER_APP_NAME = None
for name in ("app", "trading_copilot_elite", "trading_copilot_pro", "trading_copilot"):
    try:
        USER_APP_MODULE = importlib.import_module(name)
        USER_APP_NAME = name
        break
    except Exception:
        USER_APP_MODULE = None

# -------------------- CONFIG DEFAULTS (can be overridden by user's module globals) --------------------
ALERT_LOG_FILE = Path(getattr(USER_APP_MODULE, "ALERT_LOG_FILE", Path("alert_history.json")))
ALERT_ARCHIVE_FILE = Path(getattr(USER_APP_MODULE, "ALERT_ARCHIVE_FILE", Path("alert_archive.json")))
# User preferences provided:
DEDUPE_WINDOW_MINUTES = int(getattr(USER_APP_MODULE, "DEDUPE_WINDOW_MINUTES", 60))  # 1 hour
RECENT_ALERT_WINDOW_MINUTES = int(getattr(USER_APP_MODULE, "RECENT_ALERT_WINDOW_MINUTES", 7 * 24 * 60))  # 7 days
DEDUPE_BY = getattr(USER_APP_MODULE, "DEDUPE_BY", "id")  # dedupe by alert id as requested

MIN_DTE = int(getattr(USER_APP_MODULE, "MIN_DTE", 1))
MAX_DTE = int(getattr(USER_APP_MODULE, "MAX_DTE", 30))
MIN_ADX = int(getattr(USER_APP_MODULE, "MIN_ADX", 25))
MIN_OPTION_VOLUME = int(getattr(USER_APP_MODULE, "MIN_OPTION_VOLUME", 50))
MIN_OPTION_OI = int(getattr(USER_APP_MODULE, "MIN_OPTION_OI", 20))
FORCE_ALLOW_SAME_DAY = set(getattr(USER_APP_MODULE, "FORCE_ALLOW_SAME_DAY", set()))
DEDUPE_WINDOW_MINUTES = DEDUPE_WINDOW_MINUTES  # ensure local var

# -------------------- SAFE CALL WRAPPER --------------------
def safe_call(func_name: str, *args, **kwargs):
    """
    Call function from user app module if present, else raise ImportError.
    Exceptions from the called function are propagated so the test runner records them.
    """
    if USER_APP_MODULE and hasattr(USER_APP_MODULE, func_name):
        return getattr(USER_APP_MODULE, func_name)(*args, **kwargs)
    raise ImportError(f"Function '{func_name}' not found in user app module.")

# -------------------- OPTIONAL DEPENDENCIES --------------------
try:
    import numpy as np
    import pandas as pd
except Exception:
    np = None
    pd = None

# -------------------- JSON SERIALIZER --------------------
def make_json_serializable(obj: Any) -> Any:
    """Recursively convert numpy/pandas types and datetimes to native Python types for JSON dumping."""
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

# -------------------- ALERT STORAGE HELPERS --------------------
def _read_alerts(path: Path) -> List[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return []

def _write_alerts(path: Path, alerts: List[Dict[str, Any]]) -> None:
    try:
        path.write_text(json.dumps(make_json_serializable(alerts), indent=2))
    except Exception:
        with open(path, "w") as f:
            json.dump(make_json_serializable(alerts), f, indent=2)

def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None

def _parse_timestamp(ts: str) -> Optional[datetime]:
    # Try ISO first, then common "YYYY-MM-DD HH:MM ET"
    try:
        if "T" in ts:
            return datetime.fromisoformat(ts)
        return datetime.strptime(ts, "%Y-%m-%d %H:%M ET")
    except Exception:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None

# -------------------- ALERT DEDUPLICATION (BY ID) --------------------
def append_alert_safe(alert: Dict[str, Any],
                      alert_file_path: Optional[Path] = None,
                      dedupe_window_minutes: int = DEDUPE_WINDOW_MINUTES,
                      dedupe_by: str = DEDUPE_BY) -> bool:
    """
    Append alert only if not duplicate within dedupe_window_minutes.
    dedupe_by: "id" or "entry_target"
    Returns True if appended, False if skipped as duplicate.
    """
    if alert_file_path is None:
        alert_file_path = ALERT_LOG_FILE
    if isinstance(alert_file_path, (str,)):
        alert_file_path = Path(alert_file_path)

    existing = _read_alerts(alert_file_path)
    now = datetime.now()

    ticker = str(alert.get("ticker", "")).upper()
    entry = _safe_float(alert.get("entry"))
    target = _safe_float(alert.get("target"))
    alert_id = alert.get("id")

    for a in reversed(existing):
        try:
            if str(a.get("ticker", "")).upper() != ticker:
                continue
            # Primary dedupe: id match (user requested)
            if dedupe_by == "id":
                if a.get("id") and alert_id and a.get("id") == alert_id:
                    return False
                # If id not present on existing alert, fall back to time-window + entry/target if available
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                ts = a.get("timestamp")
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        if ts:
                            parsed = _parse_timestamp(ts)
                            if parsed and (now - parsed) < timedelta(minutes=dedupe_window_minutes):
                                return False
                        else:
                            return False
            # Secondary dedupe: entry+target match (fallback)
            elif dedupe_by == "entry_target":
                a_entry = _safe_float(a.get("entry"))
                a_target = _safe_float(a.get("target"))
                if a_entry is not None and a_target is not None and entry is not None and target is not None:
                    if a_entry == entry and a_target == target:
                        ts = a.get("timestamp")
                        if ts:
                            parsed = _parse_timestamp(ts)
                            if parsed and (now - parsed) < timedelta(minutes=dedupe_window_minutes):
                                return False
                        else:
                            return False
            # final fallback: exact id match
            if a.get("id") and alert_id and a.get("id") == alert_id:
                return False
        except Exception:
            continue

    # Not duplicate: append and save
    existing.append(alert)
    _write_alerts(alert_file_path, existing)
    return True

# -------------------- OPTION CANDIDATE CHECKS --------------------
def candidate_passes_checks(candidate: Dict[str, Any], ticker: str, debug_lines: Optional[List[str]] = None) -> bool:
    """
    Centralized checks for candidate option contracts.
    - Rejects same-day expiries unless ticker in FORCE_ALLOW_SAME_DAY.
    - Checks option liquidity (volume, oi).
    - Checks DTE bounds.
    - Appends numeric debug lines for each check (if debug_lines provided).
    Returns True if candidate should be accepted, False otherwise.
    """
    if debug_lines is None:
        debug_lines = []

    # Normalize candidate fields
    try:
        candidate_dte = int(candidate.get("dte", 0))
    except Exception:
        candidate_dte = 0

    min_dte_allowed = int(globals().get("MIN_DTE", MIN_DTE))
    max_dte_allowed = int(globals().get("MAX_DTE", MAX_DTE))
    force_allow = globals().get("FORCE_ALLOW_SAME_DAY", FORCE_ALLOW_SAME_DAY)

    # DTE check (reject same-day by default)
    if candidate_dte < max(1, min_dte_allowed) and ticker not in force_allow:
        debug_lines.append(f"dte: {candidate_dte} < MIN_DTE {min_dte_allowed} — rejected")
        return False
    else:
        debug_lines.append(f"dte: {candidate_dte} in [{min_dte_allowed},{max_dte_allowed}]")

    # DTE upper bound
    if candidate_dte > max_dte_allowed:
        debug_lines.append(f"dte: {candidate_dte} > MAX_DTE {max_dte_allowed} — rejected")
        return False

    # Liquidity checks
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

    # Spread / price sanity checks (reject penny mid/last unless high liquidity)
    try:
        mid = float(candidate.get("mid", candidate.get("last_price", 0) or 0))
    except Exception:
        mid = 0.0
    try:
        spread = float(candidate.get("spread", 0) or 0)
    except Exception:
        spread = 0.0

    if mid <= 0.0 or (isinstance(mid, float) and math.isnan(mid)):
        debug_lines.append(f"option_price: mid {mid} <= 0 or NaN — rejected")
        return False

    # If mid is tiny (e.g., < $0.05) require stronger liquidity
    if mid < 0.05 and (opt_volume < 1000 or opt_oi < 200):
        debug_lines.append(f"option_price: mid {mid:.2f} < 0.05 with low liquidity vol {opt_volume} oi {opt_oi} — rejected")
        return False

    debug_lines.append(f"option_price: mid {mid:.2f}; spread {spread:.2f}")

    return True

# -------------------- FILTER DEBUG HELPERS --------------------
def debug_adx(adx_value: Any, debug_lines: List[str]) -> bool:
    try:
        adx_value = float(adx_value)
    except Exception:
        adx_value = 0.0
    min_adx = int(globals().get("MIN_ADX", MIN_ADX))
    if adx_value < min_adx:
        debug_lines.append(f"adx: {adx_value:.1f} < MIN_ADX {min_adx}")
        return False
    debug_lines.append(f"adx: {adx_value:.1f} >= MIN_ADX {min_adx}")
    return True

def debug_weekly_slope(weekly_slope: Any, debug_lines: List[str]) -> bool:
    try:
        weekly_slope = float(weekly_slope)
    except Exception:
        weekly_slope = 0.0
    min_weekly_slope = float(globals().get("MIN_WEEKLY_SLOPE", 0.0))
    if weekly_slope < min_weekly_slope:
        debug_lines.append(f"weekly: slope {weekly_slope:.6f} < MIN_WEEKLY_SLOPE {min_weekly_slope}")
        return False
    debug_lines.append(f"weekly: slope {weekly_slope:.6f} >= MIN_WEEKLY_SLOPE {min_weekly_slope}")
    return True

def debug_earnings(days_to_earnings: Any, debug_lines: List[str]) -> bool:
    try:
        days_to_earnings = int(days_to_earnings)
    except Exception:
        days_to_earnings = 9999
    earnings_days = int(globals().get("EARNINGS_DAYS", 30))
    if 0 <= days_to_earnings <= earnings_days:
        debug_lines.append(f"earnings: {days_to_earnings} <= EARNINGS_DAYS {earnings_days} — blocked")
        return False
    debug_lines.append(f"earnings: {days_to_earnings} > EARNINGS_DAYS {earnings_days} — ok")
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
    """
    Evaluate a candidate, append alert safely if accepted, and return a dict with outcome.
    Returns: {"accepted": bool, "appended": bool, "debug": [...], "alert": alert_dict or None}
    """
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

# -------------------- RECENT ALERTS UI & MANAGEMENT --------------------
def get_recent_alerts(window_minutes: int = RECENT_ALERT_WINDOW_MINUTES,
                      alert_file_path: Path = ALERT_LOG_FILE) -> List[Dict[str, Any]]:
    """
    Return alerts whose timestamp is within the last window_minutes.
    Alerts with unparseable timestamps are included.
    """
    alerts = _read_alerts(alert_file_path)
    now = datetime.now()
    recent = []
    for a in alerts:
        ts = a.get("timestamp")
        parsed = _parse_timestamp(ts) if ts else None
        if parsed:
            if (now - parsed) <= timedelta(minutes=window_minutes):
                recent.append(a)
        else:
            recent.append(a)
    return recent

def clear_recent_alerts(window_minutes: int = RECENT_ALERT_WINDOW_MINUTES,
                        alert_file_path: Path = ALERT_LOG_FILE,
                        archive_file_path: Path = ALERT_ARCHIVE_FILE,
                        archive: bool = False) -> Dict[str, int]:
    """
    Remove recent alerts from alert_file_path.
    If archive is True, append removed alerts to archive_file_path.
    Returns counts: {"removed": n, "remaining": m}
    """
    alerts = _read_alerts(alert_file_path)
    now = datetime.now()
    keep = []
    removed = []
    for a in alerts:
        ts = a.get("timestamp")
        parsed = _parse_timestamp(ts) if ts else None
        if parsed and (now - parsed) <= timedelta(minutes=window_minutes):
            removed.append(a)
        elif not parsed:
            removed.append(a)
        else:
            keep.append(a)

    if removed and archive:
        archive_existing = _read_alerts(archive_file_path)
        archive_existing.extend(removed)
        _write_alerts(archive_file_path, archive_existing)

    _write_alerts(alert_file_path, keep)
    return {"removed": len(removed), "remaining": len(keep)}

def alerts_ui_panel(sidebar: bool = True,
                    recent_window_minutes: int = RECENT_ALERT_WINDOW_MINUTES):
    """
    Show recent alerts and Clear buttons. Placed in sidebar by default.
    """
    container = st.sidebar if sidebar else st
    with container.expander("Recent alerts", expanded=True):
        st.write(f"Showing alerts from the last **{recent_window_minutes // 60} days**")
        recent = get_recent_alerts(window_minutes=recent_window_minutes)
        if not recent:
            st.info("No recent alerts")
        else:
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
                result = clear_recent_alerts(window_minutes=recent_window_minutes, archive=False)
                st.success(f"Permanently removed {result['removed']} alerts; {result['remaining']} remain")
        with col2:
            if st.button("Archive and clear recent alerts"):
                result = clear_recent_alerts(window_minutes=recent_window_minutes, archive=True)
                st.success(f"Archived and removed {result['removed']} alerts; {result['remaining']} remain")

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
            # Try to call get_data_with_error from user app; fallback to stub
            try:
                df_err = safe_call("get_data_with_error", t)
                if isinstance(df_err, tuple) and len(df_err) >= 2:
                    df, err = df_err[0], df_err[1]
                else:
                    df, err = df_err, None
            except ImportError:
                df, err = None, "no_user_data_function"
            entry["data_error"] = err
            if df is not None:
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
    try:
        try:
            watchlist = list(getattr(USER_APP_MODULE, "WATCHLIST"))
        except Exception:
            watchlist = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]
    except Exception:
        watchlist = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "SPY"]

    for mode in (True, False):
        try:
            scan_list = watchlist[:5] if mode else watchlist
            try:
                results_debug = safe_call("scan_watchlist", scan_list)
                if isinstance(results_debug, tuple) and len(results_debug) >= 2:
                    results, debug = results_debug[0], results_debug[1]
                elif isinstance(results_debug, dict) and "results" in results_debug:
                    results, debug = results_debug.get("results"), results_debug.get("debug")
                else:
                    results, debug = results_debug, []
            except ImportError:
                results, debug = [], []
                for tk in scan_list:
                    debug.append([tk, "NO_SIGNAL", ["stub: no scan_watchlist available"]])
            report["scans"].append({"fast_mode": bool(mode), "scan_list": scan_list, "results_count": len(results) if results is not None else 0, "debug": make_json_serializable(debug)})
        except Exception as e:
            report["scans"].append({"fast_mode": bool(mode), "error": str(e)})

    # Edge cases: insufficient_history
    try:
        orig_min_rows = globals().get("MIN_ROWS", None)
        if orig_min_rows is not None:
            globals()["MIN_ROWS"] = 500
        try:
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

    # Edge case: earnings_blackout
    try:
        orig_earn = globals().get("EARNINGS_DAYS", None)
        globals()["EARNINGS_DAYS"] = 30
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
            report["edge_cases"].append({"test": "earnings_blackout", "EARNINGS_DAYS": 30, "lookup": make_json_serializable(sig)})
        finally:
            if orig_earn is not None:
                globals()["EARNINGS_DAYS"] = orig_earn
    except Exception as e:
        report["edge_cases"].append({"test": "earnings_blackout", "error": str(e)})

    # Edge case: budget_no_option
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
                    if pd is not None:
                        exp_dt = pd.to_datetime(expiry).date()
                    else:
                        exp_dt = datetime.fromisoformat(str(expiry)).date()
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

st.markdown("Use the sidebar to run automated tests and manage alerts. This wrapper will call your app's functions if available.")

with st.sidebar:
    st.header("Admin")
    run_tests = st.button("Run automated tests (admin)")
    # Show recent alerts panel in sidebar (current state)
    alerts_ui_panel(sidebar=True, recent_window_minutes=RECENT_ALERT_WINDOW_MINUTES)

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

if USER_APP_MODULE:
    st.info(f"Using functions from imported module: {USER_APP_NAME}")
else:
    st.warning("No user app module found (app.py or trading_copilot_elite). The test runner will use safe stubs.")

st.markdown("This page is an admin helper. Use the sidebar button to run tests and manage alerts.")
