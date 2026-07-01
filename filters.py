# filters.py
from typing import Dict, Any, List, Optional
import math
from config import Config

def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def structured_debug(msg: str, **kwargs) -> Dict[str, Any]:
    entry = {"msg": msg}
    entry.update(kwargs)
    return entry

def debug_adx(adx_value: Any, cfg: Config, debug: List[Dict]) -> bool:
    val = _safe_float(adx_value) or 0.0
    ok = val >= cfg.min_adx
    debug.append(structured_debug("adx", value=val, min=cfg.min_adx, pass=ok))
    return ok

def debug_weekly_slope(weekly_slope: Any, min_weekly_slope: float, debug: List[Dict]) -> bool:
    val = _safe_float(weekly_slope) or 0.0
    ok = val >= min_weekly_slope
    debug.append(structured_debug("weekly_slope", value=val, min=min_weekly_slope, pass=ok))
    return ok

def debug_earnings(days_to_earnings: Any, cfg: Config, debug: List[Dict]) -> bool:
    try:
        days = int(days_to_earnings)
    except Exception:
        days = 9999
    ok = not (0 <= days <= cfg.recent_alert_window_minutes)
    debug.append(structured_debug("earnings", days=days, blocked=not ok))
    return ok

def candidate_passes_checks(candidate: Dict[str, Any], cfg: Config, debug: Optional[List[Dict]] = None) -> bool:
    if debug is None:
        debug = []

    try:
        dte = int(candidate.get("dte", 0))
    except Exception:
        dte = 0
    if dte < max(1, cfg.min_dte) and candidate.get("ticker", "").upper() not in cfg.force_allow_same_day:
        debug.append(structured_debug("dte", value=dte, min=cfg.min_dte, pass=False))
        return False
    debug.append(structured_debug("dte", value=dte, min=cfg.min_dte, pass=True))

    if dte > cfg.max_dte:
        debug.append(structured_debug("dte_max", value=dte, max=cfg.max_dte, pass=False))
        return False

    vol = int(candidate.get("volume", 0) or 0)
    oi = int(candidate.get("oi", 0) or 0)
    vol_ok = vol >= cfg.min_option_volume
    oi_ok = oi >= cfg.min_option_oi
    debug.append(structured_debug("liquidity", volume=vol, min_volume=cfg.min_option_volume, oi=oi, min_oi=cfg.min_option_oi, pass=(vol_ok and oi_ok)))
    if not (vol_ok and oi_ok):
        return False

    mid = _safe_float(candidate.get("mid", candidate.get("last_price", 0))) or 0.0
    spread = _safe_float(candidate.get("spread", 0)) or 0.0
    if mid <= 0 or math.isnan(mid):
        debug.append(structured_debug("price", mid=mid, pass=False, reason="mid<=0 or NaN"))
        return False
    if mid < 0.05 and (vol < 1000 or oi < 200):
        debug.append(structured_debug("price", mid=mid, pass=False, reason="tiny mid with low liquidity"))
        return False
    debug.append(structured_debug("price", mid=mid, spread=spread, pass=True))
    return True
