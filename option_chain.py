#!/usr/bin/env python3
"""
option_chain.py — fetch and rank the option chain

Extracted out of app.py — third piece of the same split (gh_sync.py, then
journal_store.py). This is the "get contracts from Yahoo and pick the best
one" layer: chain fetch with retry, and strike selection.

WHAT DID NOT MOVE
-----------------
check_manual_contract() stayed in app.py. It answers "does the underlying
signal + this specific contract meet the rules" and for that it calls
get_data() / compute() / drop_partial_bar() / get_spy_regime() / analyze() /
get_settings_key() — app.py's entire signal-evaluation pipeline, six
dependencies for one function. That is a fundamentally different, larger
piece (the market-data + signal layer) than "fetch and rank a chain," and
threading six callables through this module for one caller would be worse
than leaving it where it is. It still imports _fetch_chain_with_retry from
here, the one piece of this module it actually needs.

DEPENDENCY INJECTION
---------------------
get_option_data() takes `min_dte`, `atr_tgt_mult`, `budget_max` and
`is_market_open` as parameters instead of reading MIN_DTE / ATR_TGT_MULT /
BUDGET_MAX / is_market_open() as app.py globals/functions — same pattern
signal_core.py, data_source.py, gh_sync.py and journal_store.py already use
in this repo, for the same reason: this module doesn't share a namespace
with app.py's sidebar values or its is_market_open() anymore.

No selftest here, for the same reason gh_sync.py and journal_store.py have
none: every function here makes live Yahoo calls end to end (chain fetches
have no meaningful offline fake — the whole point is real bid/ask/volume/OI
from the market). Correctness is exercised through the app.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import streamlit as st
import yfinance as yf

from rate_limit import RATE_LIMITER as _rl, is_rate_limit_error as _is_rate_limit_error

logger = logging.getLogger(__name__)

_OPT_RETRY_ATTEMPTS = 4     # was 3 — one extra attempt before giving up.
_OPT_RETRY_DELAY    = 4.0   # was 2.0 — Yahoo throttles the options endpoints
                            # aggressively from shared cloud IPs. A longer first
                            # backoff (4s → 8s → 16s with the ×2 growth below)
                            # gives the limiter time to reset, turning most
                            # "rate limited" errors into slow-but-successful
                            # loads instead of a hard failure on the first ticker.
_OPT_EXPIRY_DELAY   = 0.6   # was 0.4 — slightly more spacing between the per-
                            # expiry chain fetches so a single ticker doesn't
                            # burst 5 calls in ~2s and trip the limiter itself.
_OPT_MAX_EXPIRIES   = 3   # was 5. Each expiry = one full-chain fetch, so 5
                          # expiries = ~6 Yahoo calls for ONE ticker — the single
                          # biggest source of rate-limit hits. Back to 3 cuts
                          # per-ticker call volume ~40%. The DTE-adequacy check
                          # already flags contracts that are too short-dated, so
                          # 3 nearest valid expiries is enough for a swing target.


def _fetch_chain_with_retry(stock, expiry: str):
    delay = _OPT_RETRY_DELAY
    for attempt in range(_OPT_RETRY_ATTEMPTS):
        _rl.wait()
        try:
            return stock.option_chain(expiry)
        except Exception as e:
            msg = str(e).lower()
            if ("too many requests" in msg or "rate limit" in msg or "429" in msg) \
               and attempt < _OPT_RETRY_ATTEMPTS - 1:
                logger.warning("Rate limited chain %s %s; backoff %ss", stock.ticker, expiry, delay)
                time.sleep(delay); delay *= 2; continue
            raise
    return None


@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(ticker: str, min_dte: int) -> dict:
    # BUG FIX: min_dte is part of the cache key. Previously the MIN_DTE
    # sidebar global was read as a closure, so changing Min DTE did NOT
    # invalidate this 15-minute cache — stale expiries kept being served.
    try:
        stock = yf.Ticker(ticker)
        # The initial expiries fetch is the call most often rate-limited (it's
        # the first Yahoo hit on the Options tab). Previously it retried only
        # ONCE after a fixed 3s sleep, then failed hard — which is exactly the
        # "rate limited on the first ticker" error. Give it the same escalating
        # backoff as the chain fetches so a throttle becomes a slow success.
        all_expiries = None
        delay = _OPT_RETRY_DELAY
        for attempt in range(_OPT_RETRY_ATTEMPTS):
            _rl.wait()
            try:
                all_expiries = stock.options
                # BUG FIX: yfinance returns an EMPTY LIST when throttled rather
                # than raising, so an empty result used to skip the retry loop
                # entirely and surface as "No option chain available" — which
                # reads like the stock has no options at all. For a name like
                # KO with thousands of listed contracts that is never true; it
                # is a throttle. Treat empty as retryable.
                if all_expiries:
                    break
                if attempt < _OPT_RETRY_ATTEMPTS - 1:
                    logger.warning("Empty expiry list for %s (likely throttled); "
                                   "retry in %ss", ticker, delay)
                    time.sleep(delay); delay *= 2; continue
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < _OPT_RETRY_ATTEMPTS - 1:
                    logger.warning("Rate limited options(%s); backoff %ss", ticker, delay)
                    time.sleep(delay); delay *= 2; continue
                raise
        if not all_expiries:
            # Say what actually happened. The previous wording implied the
            # underlying is not optionable, sending you to check the ticker
            # when the real answer is "wait a minute and try again".
            return {"error": f"Yahoo returned no expiries for {ticker} after "
                             f"{_OPT_RETRY_ATTEMPTS} attempts — almost always a "
                             f"temporary rate limit, not a missing chain. "
                             f"Try again shortly.",
                    "expiries": []}

        today   = pd.Timestamp.today().normalize()
        result  = []
        checked = 0
        for expiry in all_expiries:
            if checked >= _OPT_MAX_EXPIRIES:
                break
            try:
                dte = (pd.Timestamp(expiry) - today).days
            except Exception:
                continue
            if dte < min_dte:
                continue
            checked += 1
            try:
                time.sleep(_OPT_EXPIRY_DELAY)
                chain = _fetch_chain_with_retry(stock, expiry)
                if chain is None:
                    continue
                result.append({"expiry":expiry,"dte":dte,
                                "calls":chain.calls.fillna(0),
                                "puts":chain.puts.fillna(0)})
            except Exception as e:
                logger.exception("Skipping expiry %s for %s: %s", expiry, ticker, e)
        if not result:
            return {"error":"No valid expiries found","expiries":[]}
        return {"error":None,"expiries":result}
    except Exception as e:
        msg = str(e)
        if _is_rate_limit_error(Exception(msg)):
            return {"error":"Rate limited by Yahoo Finance — try again shortly","expiries":[]}
        return {"error":f"Option chain fetch failed ({msg})","expiries":[]}


def get_option_data(ticker: str, price: float, trend: str, strength: str,
                    min_dte: int, atr_tgt_mult: float, budget_max: float,
                    is_market_open, atr: float | None = None) -> dict:
    """
    Strike selection.

    `min_dte` / `atr_tgt_mult` / `budget_max` / `is_market_open` are passed
    in — see the module docstring. `is_market_open` is a zero-arg callable
    (app.py's is_market_open function itself), used only for a diagnostic
    message when no live bid is found.

    BUG FIX: the 'Strong' branch previously had only ONE bound —
        opts[opts["strike"] <= price * 1.02]      (bullish)
    which accepted EVERY strike from $1 up to 1.02×price. On SPY at $749 that
    scanned every deep-ITM call from $1 to $764. Same unbounded issue on the
    bearish side. Now both branches are two-sided windows.

    Windows are also ATR-aware where possible: a 5% band means something very
    different on a 1%-ATR index than on a 6%-ATR small cap. If ATR is supplied
    we size the window to ±2.0 ATR (floored/capped at sane percentage bounds);
    otherwise we fall back to fixed percentages.
    """
    chain_data = get_full_chain_data(ticker, min_dte)
    if chain_data.get("error"):
        return {"error": chain_data["error"]}

    # ── Build the strike window ──
    if atr and atr > 0 and price > 0:
        band = (atr * 2.0) / price               # ±2 ATR expressed as a fraction
        band = min(max(band, 0.03), 0.12)        # clamp to 3%–12%
    else:
        band = 0.05                              # fallback: ±5%

    if strength == "Strong":
        # Slightly ITM/ATM bias — but two-sided, not unbounded.
        if trend == "Bullish":
            lo_mult, hi_mult = 1.0 - band, 1.02          # ITM up to 1 band, max 2% OTM
        else:
            lo_mult, hi_mult = 0.98, 1.0 + band          # ITM up to 1 band, max 2% OTM
    else:
        lo_mult, hi_mult = 1.0 - band, 1.0 + band        # symmetric ATM window

    lo, hi = price * lo_mult, price * hi_mult

    best = None; best_score = 0.0
    # Diagnostics. "No liquid options found" on its own is a dead end — it does
    # not say whether the chain was empty, the strike window excluded
    # everything, or contracts existed and every one failed a liquidity gate.
    # Those need different responses, so count them.
    diag = {"expiries": 0, "in_window": 0, "had_bid": 0, "had_volume": 0,
            "had_oi": 0, "spread_ok": 0, "best_spread_pct": None,
            "window_lo": round(lo, 2), "window_hi": round(hi, 2)}

    for entry in chain_data["expiries"]:
        expiry, dte = entry["expiry"], entry["dte"]
        opts = entry["calls"] if trend=="Bullish" else entry["puts"]
        if opts.empty: continue
        diag["expiries"] += 1

        opts = opts[(opts["strike"] >= lo) & (opts["strike"] <= hi)]
        if opts.empty: continue
        diag["in_window"] += len(opts)

        opts = opts.copy()
        # Yahoo returns NaN (not 0) for quotes on some contracts. NaN fails every
        # comparison silently, so a NaN bid and a zero bid both mean "no quote" —
        # make that explicit rather than relying on comparison semantics.
        for _c in ("bid", "ask", "volume", "openInterest"):
            if _c in opts.columns:
                opts[_c] = opts[_c].fillna(0)
        opts["spread"] = opts["ask"] - opts["bid"]
        opts["mid"]    = (opts["ask"] + opts["bid"]) / 2

        _live = opts[(opts["mid"] > 0) & (opts["bid"] > 0)]
        diag["had_bid"] += len(_live)
        _vol = _live[_live["volume"] > 0]
        diag["had_volume"] += len(_vol)
        _oi = _vol[_vol["openInterest"] > 0]
        diag["had_oi"] += len(_oi)
        if not _oi.empty:
            _sp = (_oi["spread"] / _oi["mid"] * 100)
            diag["spread_ok"] += int((_sp <= 15.0).sum())
            _tightest = float(_sp.min())
            if diag["best_spread_pct"] is None or _tightest < diag["best_spread_pct"]:
                diag["best_spread_pct"] = round(_tightest, 1)
        # Require bid > 0 (mid can pass even when bid=0 on wide/illiquid strikes)
        # and volume > 0 (a zero-volume contract is untradeable regardless of OI).
        valid = opts[
            (opts["mid"] > 0) &
            (opts["bid"] > 0) &
            (opts["volume"] > 0) &
            (opts["spread"] / opts["mid"] <= 0.15)
        ]
        valid = valid[valid["openInterest"] > 0]   # also require some existing interest
        if valid.empty: continue
        valid = valid.copy()
        valid["liq"]   = valid["volume"] + valid["openInterest"]
        # Volume weight so zero-volume high-OI contracts don't outscore genuinely
        # active ones. volume=0 → weight 0.1; volume>0 → scales with activity.
        valid["vol_weight"] = valid["volume"].apply(lambda v: 0.1 if v == 0 else 1.0 + (v / (v + 100)))
        valid["score"] = (valid["liq"] * valid["vol_weight"]) / (1 + (valid["spread"] / (valid["mid"] + 1e-6)))

        # ── DTE ADEQUACY (theta protection) ──
        # A 2.5-ATR target typically needs ~2.5 average-range days of favourable
        # movement, and real moves are rarely straight lines — budget ~3x that,
        # plus a few days of buffer. A contract that expires before the trade can
        # realistically reach target is a theta trap no matter how liquid it is.
        # We SCALE the score rather than hard-filtering, so a very liquid short
        # contract can still win if nothing better exists — but it gets flagged.
        if atr and atr > 0:
            # Scales with the sidebar target multiplier (was hardcoded 2.5 —
            # inconsistent after the default target moved to 3.0× ATR).
            days_needed = max(5, int(atr_tgt_mult * 3))
        else:
            days_needed = 10
        if dte < days_needed:
            valid["score"] *= (dte / days_needed) ** 2   # quadratic theta penalty

        top = valid.sort_values("score", ascending=False).iloc[0]
        if top["score"] > best_score:
            best = (top, expiry, dte); best_score = top["score"]

    if best is None:
        if diag["expiries"] == 0:
            why = "no expiries came back with contracts"
        elif diag["in_window"] == 0:
            why = (f"no strikes between ${diag['window_lo']} and "
                   f"${diag['window_hi']} (the ±ATR window around "
                   f"${price:.2f})")
        elif diag["had_bid"] == 0:
            why = f"all {diag['in_window']} strikes in range had no live bid"
            # Almost always the clock, not the chain. Yahoo returns zero or NaN
            # bids for options outside regular hours, so every strike fails the
            # bid > 0 gate after the close — with no indication that the cause
            # is the time of day.
            if not is_market_open():
                why += (". The market is closed — option quotes go stale after "
                        "the bell, so this is expected outside 9:30-16:00 ET "
                        "rather than a problem with the chain")
        elif diag["had_volume"] == 0:
            why = (f"{diag['had_bid']} strikes had a bid but none traded "
                   f"today (volume 0)")
        elif diag["had_oi"] == 0:
            why = f"{diag['had_volume']} strikes traded but none had open interest"
        elif diag["spread_ok"] == 0:
            tight = diag["best_spread_pct"]
            why = (f"{diag['had_oi']} strikes passed liquidity but every spread "
                   f"exceeded 15% of mid — tightest was {tight}%")
        else:
            why = "contracts passed the filters but none scored"
        return {"error": f"No liquid options found: {why}.", "diag": diag}

    row, expiry, dte = best
    days_needed = max(5, int(atr_tgt_mult * 3)) if (atr and atr > 0) else 10
    return {"label":"CALL" if trend=="Bullish" else "PUT",
            "strike":round(float(row["strike"]),2),
            "expiry":expiry,"mid":round(float(row["mid"]),2),
            "last_price":round(float(row.get("lastPrice",0)),2),
            "volume":int(row.get("volume",0)),"oi":int(row.get("openInterest",0)),
            "spread":round(float(row["spread"]),2),"dte":dte,
            "strike_lo":round(lo,2),"strike_hi":round(hi,2),
            "days_needed":days_needed,
            "dte_adequate":dte >= days_needed,
            "is_budget":row["mid"]<=budget_max}
