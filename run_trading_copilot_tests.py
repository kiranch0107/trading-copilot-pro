#!/usr/bin/env python3
import json, os, sys, time
from datetime import datetime
# Adjust import path if needed so the app module can be imported
APP_PATH = "/mount/src/trading-copilot-pro"
if APP_PATH not in sys.path:
    sys.path.insert(0, APP_PATH)

# Import the functions from your app file. If your app is app.py, import as module.
try:
    import app as trading_app
except Exception as e:
    print(json.dumps({"error":"failed_import","exception":str(e)}))
    raise

def safe_lookup(ticker, period="3mo", interval="1d"):
    out = {"ticker": ticker}
    try:
        df, err = trading_app.get_data_with_error(ticker, period=period, interval=interval)
        out["data_error"] = err
        if df is not None:
            last_key = str(df.index[-1])
            dfc = trading_app.compute_cached(ticker, last_key, df)
            weekly = trading_app.get_weekly_trend(ticker)
            spy_regime = trading_app.get_spy_regime()
            sig = trading_app.generate_swing_signal(ticker, dfc, weekly, spy_regime)
            out["signal"] = sig
    except Exception as e:
        out["exception"] = str(e)
    return out

def run_scan(mode_fast):
    # set sidebar-like globals used by functions
    trading_app.FAST_MODE = mode_fast
    scan_list = trading_app.WATCHLIST[:5] if mode_fast else trading_app.WATCHLIST
    results, debug = trading_app.scan_watchlist(scan_list) if hasattr(trading_app, "scan_watchlist") else ([], [])
    return {"fast_mode": mode_fast, "scan_list": scan_list, "results": results, "debug": debug}

report = {"meta":{"timestamp":datetime.utcnow().isoformat()+"Z"}, "single_lookup":[], "scans":[], "edge_cases":[], "alerts_file": None}

# Single ticker checks
for t in ["SPY","AAPL","TSLA"]:
    report["single_lookup"].append(safe_lookup(t))

# Scans: fast and full
report["scans"].append(run_scan(mode_fast=True))
report["scans"].append(run_scan(mode_fast=False))

# Edge case 1: Insufficient history
orig_min_rows = trading_app.MIN_ROWS
trading_app.MIN_ROWS = 500
res, debug = trading_app.scan_watchlist(trading_app.WATCHLIST)
report["edge_cases"].append({"test":"insufficient_history","MIN_ROWS":500,"results_count":len(res),"debug_sample":debug[:10]})
trading_app.MIN_ROWS = orig_min_rows

# Edge case 2: Earnings blackout
orig_earn = trading_app.EARNINGS_DAYS
trading_app.EARNINGS_DAYS = 30
lk = safe_lookup("AAPL")
report["edge_cases"].append({"test":"earnings_blackout","EARNINGS_DAYS":30,"lookup":lk})
trading_app.EARNINGS_DAYS = orig_earn

# Edge case 3: Budget forcing no option
orig_budget = trading_app.BUDGET_MAX
trading_app.BUDGET_MAX = 0.01
lk = safe_lookup("AAPL")
report["edge_cases"].append({"test":"budget_no_option","BUDGET_MAX":0.01,"lookup":lk})
trading_app.BUDGET_MAX = orig_budget

# DTE verification: collect option dte values for a liquid ticker
try:
    chain = trading_app.get_full_chain_data("SPY")
    dtes = []
    if "expiries" in chain:
        for e in chain["expiries"]:
            expiry = e["expiry"]
            try:
                exp_dt = trading_app.pd.to_datetime(expiry).date()
            except Exception:
                try:
                    exp_dt = datetime.strptime(str(expiry), "%Y-%m-%d").date()
                except Exception:
                    continue
            dte = (exp_dt - datetime.now().date()).days
            dtes.append(int(dte))
    report["dte_check"] = {"dtes": dtes, "min_allowed": trading_app.MIN_DTE, "max_allowed": trading_app.MAX_DTE}
except Exception as e:
    report["dte_check"] = {"error": str(e)}

# Alerts file snapshot
alerts_path = trading_app.ALERT_LOG_FILE
if alerts_path.exists():
    try:
        report["alerts_file"] = json.loads(alerts_path.read_text())
    except Exception as e:
        report["alerts_file_error"] = str(e)
else:
    report["alerts_file"] = []

# Save report
out_path = "test_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, default=str)

print(json.dumps({"status":"done","report_file":out_path}))
