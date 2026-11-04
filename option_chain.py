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

No selftest here: every function makes live Yahoo calls end to end, and a
chain fetch has no meaningful offline fake — the whole point is real
bid/ask/volume/OI from the market. Correctness is exercised through the app.
(gh_sync.py and journal_store.py used to be named here as the same case. Both
have selftests now — their pure parts turned out to be fakeable after all —
so this file is on its own.)

The one number here that IS testable offline is the bid-ask ceiling, and it
does not live here: it is derived in risk_params.MAX_OPTION_SPREAD_PCT and
asserted by that module's selftest and by consistency_check.py.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
# Streamlit is the APP's dependency. The selector below is read by the
# scanner too (group C, 2026-10-28: one selector for both), and the
# scanner's workflow does not install Streamlit, so the import is optional:
# without it the chain cache decorator is a no-op and nothing else here
# needs it.
try:
    import streamlit as st
except ImportError:                                   # unattended runners
    class _NoStreamlit:
        @staticmethod
        def cache_data(*_a, **_k):
            return lambda fn: fn
    st = _NoStreamlit()
import yfinance as yf

import risk_params
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


def et_today() -> pd.Timestamp:
    """
    Today's date in New York, as a normalised Timestamp: the date DTE is
    counted from (review 2026-11-03, N3). pandas' today() reads the
    HOST clock; the app's server and the Actions runners are UTC, so after
    20:00 ET (19:00 in summer) a 46-DTE contract read as 45, passed the
    window, and the journal (which dates the trade in ET) then refused it.
    exit_monitor.days_to_expiry() was fixed for the same defect earlier.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    return pd.Timestamp(_dt.now(ZoneInfo("America/New_York")).date())


def sessions_to_time_exit(dte: int, dte_exit: int | None = None) -> int:
    """
    About how many trading sessions a contract with `dte` calendar days left
    is held before the structure's time exit sells it at `dte_exit` days left
    (five sessions in seven days; holidays ignored, so slightly long).

    THE THETA CHECK'S CLOCK (review 2026-11-03 hygiene; owner: "go with
    1-4"). The check compared DTE with the ~12 sessions a 4-ATR target needs,
    and with the window's floor at 21 it could never fail: "theta adequate ✅"
    on every contract. The structure sells at 7 DTE, so a 21-DTE contract is
    held about 10 sessions, not 21, and that is what the target has.
    """
    if dte_exit is None:
        dte_exit = int(risk_params.MEASURED_OPTION_STRUCTURE["dte_exit"])
    return max(0, int((int(dte) - int(dte_exit)) * 5 / 7))


def target_sessions_needed(atr_tgt_mult: float, atr: float | None = None) -> int:
    """The selector's long-standing heuristic for the sessions a target needs:
    about three per ATR of target distance, at least five; ten without an ATR."""
    return max(5, int(atr_tgt_mult * 3)) if (atr and atr > 0) else 10


def in_dte_window(dte: int, min_dte: int, max_dte: int | None = None) -> bool:
    """
    Whether an expiry is inside the window the selector reads: at least
    `min_dte` out and, when `max_dte` is given, at most that far. The app
    passes the measured structure's 21-45 (risk_params, BACKLOG 32; review
    2026-10-28 F1): without the ceiling the third listed expiry on a
    monthly-only name could be 50-60 DTE, a contract the journal then
    refuses to log as a signal trade. The scanner's own fetch applies the
    same window through this function.
    """
    if dte < min_dte:
        return False
    return max_dte is None or dte <= max_dte


@st.cache_data(ttl=900, show_spinner=False)
def get_full_chain_data(ticker: str, min_dte: int, max_dte: int | None = None) -> dict:
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

        today   = et_today()                     # ET, not the host clock (N3)
        result  = []
        checked = 0
        for expiry in all_expiries:
            if checked >= _OPT_MAX_EXPIRIES:
                break
            try:
                dte = (pd.Timestamp(expiry) - today).days
            except Exception:
                continue
            if not in_dte_window(dte, min_dte, max_dte):
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
                    is_market_open, atr: float | None = None,
                    chain_data: dict | None = None,
                    max_dte: int | None = None,
                    target: float | None = None) -> dict:
    """
    Strike selection.

    NEAREST THE MONEY, NEVER BEYOND THE TARGET (review 2026-11-03 D2; owner:
    "go with D2"). The measured structure buys at the money (option_backtest:
    strike = round(the entry open)); this used to name the MOST LIQUID strike
    in a +/-2 ATR window, so on a price of 100 it could name the 103 call, and
    with a resistance-capped target of 102.6 a strike above the alert's own
    target (a correct signal that pins at the target expires worthless). Now:
    among the strikes that pass every liquidity gate, inside the window AND
    within risk_params.MEASURED_OPTION_STRUCTURE["moneyness_pct"] of `price`,
    the one nearest `price`; liquidity breaks a tie (the same strike on two
    expiries). With `target`, a call above it (a put below it) is never named.

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
    # chain_data is injectable so the selftest can exercise strike selection and
    # the rejection messages offline. This module picks the contract real money
    # is spent on and had NO test at all (BACKLOG 10) precisely because every
    # path needed a live Yahoo chain.
    if chain_data is None:
        chain_data = get_full_chain_data(ticker, min_dte, max_dte)
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
    # D2: inside the structure's moneyness band, and never beyond the target.
    _m = float(risk_params.MEASURED_OPTION_STRUCTURE["moneyness_pct"]) / 100.0
    lo, hi = max(lo, price * (1 - _m)), min(hi, price * (1 + _m))
    try:
        _tg = float(target) if target is not None else None
    except (TypeError, ValueError):
        _tg = None
    if _tg is not None and _tg > 0:
        if trend == "Bullish":
            hi = min(hi, _tg)
        else:
            lo = max(lo, _tg)

    best = None; best_key = None
    # Diagnostics. "No liquid options found" on its own is a dead end — it does
    # not say whether the chain was empty, the strike window excluded
    # everything, or contracts existed and every one failed a liquidity gate.
    # Those need different responses, so count them.
    diag = {"expiries": 0, "in_window": 0, "had_bid": 0, "had_volume": 0,
            "had_oi": 0, "spread_ok": 0, "best_spread_pct": None,
            "tightest": None,
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

        # An inverted quote (ask below bid) is a stale or crossed print, not a
        # market; its "spread" is negative and would pass any ceiling
        # (BACKLOG 36 group A).
        _live = opts[(opts["mid"] > 0) & (opts["bid"] > 0) & (opts["ask"] >= opts["bid"])]
        diag["had_bid"] += len(_live)
        _vol = _live[_live["volume"] > 0]
        diag["had_volume"] += len(_vol)
        _oi = _vol[_vol["openInterest"] > 0]
        diag["had_oi"] += len(_oi)
        if not _oi.empty:
            _sp = (_oi["spread"] / _oi["mid"] * 100)
            diag["spread_ok"] += int((_sp <= risk_params.MAX_OPTION_SPREAD_PCT).sum())
            _tightest = float(_sp.min())
            if diag["best_spread_pct"] is None or _tightest < diag["best_spread_pct"]:
                diag["best_spread_pct"] = round(_tightest, 1)
                # Keep the CONTRACT, not just the number. "tightest was 13.3%"
                # tells you the chain is wide; it does not tell you which strike
                # to look at, what it costs, or how far off the gate it was.
                # Compared on the same _sp series the minimum came from, so the
                # row and the percentage can never disagree.
                _row = _oi.loc[_sp.idxmin()]
                diag["tightest"] = {
                    "right": "CALL" if trend == "Bullish" else "PUT",
                    "strike": round(float(_row["strike"]), 2),
                    "expiry": expiry, "dte": dte,
                    "bid": round(float(_row["bid"]), 2),
                    "ask": round(float(_row["ask"]), 2),
                    "mid": round(float(_row["mid"]), 2),
                    "spread": round(float(_row["spread"]), 2),
                    "spread_pct": round(_tightest, 1),
                    "volume": int(_row.get("volume", 0)),
                    "oi": int(_row.get("openInterest", 0)),
                    "over_by_pct": round(
                        _tightest - risk_params.MAX_OPTION_SPREAD_PCT, 1),
                }
        # Require bid > 0 (mid can pass even when bid=0 on wide/illiquid strikes)
        # and volume > 0 (a zero-volume contract is untradeable regardless of OI).
        valid = opts[
            (opts["mid"] > 0) &
            (opts["bid"] > 0) &
            (opts["ask"] >= opts["bid"]) &          # a crossed quote is no quote
            (opts["volume"] > 0) &
            # Was 0.15. Single-sourced from risk_params.MAX_OPTION_SPREAD_PCT.
            #
            # This comment used to justify the change with "at a 15% spread this
            # signal needs a 26.5% win rate and has 23.8%". That 26.5% came from
            # a TP+200 breakeven while OPT_WIN_RATE was measured at TP+100 — the
            # basis mismatch corrected in risk_params.py on 2026-09-10. At the
            # measured basis a 15% spread needs 44.1%, and 8% needs 38.9%, so
            # NO spread clears 23.8%. The ceiling is a loss cap, not a
            # profitability threshold; read risk_params.py for the full account.
            (opts["spread"] / opts["mid"]
             <= risk_params.MAX_OPTION_SPREAD_PCT / 100.0)
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
        # Scales with the sidebar target multiplier (was hardcoded 2.5 —
        # inconsistent after the default target moved to 3.0× ATR). This
        # penalty reads calendar DTE and so only bites below the 21-DTE
        # window; the contract chosen is unchanged by design. The flag the
        # app shows reads the time-exit clock (sessions_to_time_exit).
        days_needed = target_sessions_needed(atr_tgt_mult, atr)
        if dte < days_needed:
            valid["score"] *= (dte / days_needed) ** 2   # quadratic theta penalty

        # D2: nearest the money first, liquidity second.
        valid["dist"] = (valid["strike"] - price).abs()
        top = valid.sort_values(["dist", "score"], ascending=[True, False]).iloc[0]
        _key = (float(top["dist"]), -float(top["score"]))
        if best_key is None or _key < best_key:
            best = (top, expiry, dte); best_key = _key

    if best is None:
        if diag["expiries"] == 0:
            why = "no expiries came back with contracts"
        elif diag["in_window"] == 0:
            why = (f"no strikes between ${diag['window_lo']} and "
                   f"${diag['window_hi']} (the ±ATR window around "
                   f"${price:.2f}, inside {_m * 100:g}% of the money"
                   f"{' and not beyond the target' if _tg else ''})")
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
            # The ceiling is READ, not retyped. This said "exceeded 15% of mid"
            # while the filter twelve lines up applies
            # risk_params.MAX_OPTION_SPREAD_PCT (8%), so a real rejection came
            # back self-contradicting: "every spread exceeded 15% — tightest was
            # 13.3%". Both halves cannot be true, and the reader is left unable
            # to tell whether the gate or the message is wrong.
            why = (f"{diag['had_oi']} strikes passed liquidity but every spread "
                   f"exceeded {risk_params.MAX_OPTION_SPREAD_PCT:g}% of mid "
                   f"— tightest was {tight}%")
            t = diag.get("tightest")
            if t:
                # Naming the contract is useful — it says WHERE the chain is
                # least bad and what it would cost. It is also an invitation to
                # trade a contract that just failed the gate, so the text says
                # REJECTED first and states the overshoot. The number is
                # information, not a recommendation.
                why += (f". Tightest (REJECTED, {t['over_by_pct']:+g} pts over "
                        f"the {risk_params.MAX_OPTION_SPREAD_PCT:g}% ceiling): "
                        f"{t['right']} {t['strike']:g} exp {t['expiry']} "
                        f"({t['dte']}d), bid {t['bid']:.2f} / ask {t['ask']:.2f}, "
                        f"mid {t['mid']:.2f}, spread {t['spread']:.2f}, "
                        f"vol {t['volume']}, OI {t['oi']}")
        else:
            why = "contracts passed the filters but none scored"
        return {"error": f"No liquid options found: {why}.", "diag": diag}

    row, expiry, dte = best
    days_needed = target_sessions_needed(atr_tgt_mult, atr)
    to_exit = sessions_to_time_exit(dte)
    return {"label":"CALL" if trend=="Bullish" else "PUT",
            "strike":round(float(row["strike"]),2),
            "expiry":expiry,"mid":round(float(row["mid"]),2),
            "last_price":round(float(row.get("lastPrice",0)),2),
            "volume":int(row.get("volume",0)),"oi":int(row.get("openInterest",0)),
            "spread":round(float(row["spread"]),2),"dte":dte,
            "strike_lo":round(lo,2),"strike_hi":round(hi,2),
            "moneyness_pct":round((float(row["strike"]) / price - 1) * 100, 2) if price else None,
            "days_needed":days_needed,
            "sessions_to_exit":to_exit,
            "dte_adequate":to_exit >= days_needed,
            "is_budget":row["mid"]<=budget_max}


# ══════════════════════════════════════════════════════════════════════
# SELF-TEST — synthetic chains, no network.
#
# This module chooses the contract real money is spent on and had NO test at
# all until 2026-09-11 (BACKLOG item 10). It was untestable because every path
# fetched a live Yahoo chain; get_option_data() now accepts an injected
# chain_data, so strike selection and every rejection message run offline.
# ══════════════════════════════════════════════════════════════════════
def _chain(rows, expiry="2026-10-16", dte=35):
    """One expiry built from (strike, bid, ask, volume, oi) tuples."""
    import pandas as _pd
    df = _pd.DataFrame(rows, columns=["strike", "bid", "ask", "volume",
                                      "openInterest"])
    return {"expiries": [{"expiry": expiry, "dte": dte,
                          "calls": df, "puts": df}]}


def selftest() -> int:
    print("option_chain.py selftest")
    print("=" * 70)
    # THE WINDOW HAS A CEILING WHEN ASKED FOR (review 2026-10-28 F1): the
    # measured structure is 21-45 DTE and the journal refuses a signal trade
    # outside it, so the selector must not propose a 50-DTE contract.
    assert in_dte_window(21, 21, 45) and in_dte_window(45, 21, 45)
    assert not in_dte_window(20, 21, 45) and not in_dte_window(46, 21, 45)
    assert in_dte_window(400, 21) and not in_dte_window(20, 21), "no ceiling without max_dte, as before"
    import inspect as _insp
    assert "in_dte_window(dte, min_dte, max_dte)" in _insp.getsource(get_full_chain_data), \
        "get_full_chain_data must filter expiries through in_dte_window()"
    print("dte window       : floor and ceiling through one function; the chain fetch reads it")
    _open = lambda: True                                        # noqa: E731
    price = 100.0

    def _pick(chain, **kw):
        return get_option_data("TEST", price, kw.pop("trend", "Bullish"),
                               kw.pop("strength", "Normal"), 20, 3.0,
                               kw.pop("budget_max", 1e9), _open,
                               atr=kw.pop("atr", None), chain_data=chain)

    # ── the tightest contract reported IS the tightest, across expiries ──
    # Two expiries; the better spread is in the SECOND one, so a version that
    # kept the first match rather than the minimum would fail here.
    import pandas as pd
    wide = pd.DataFrame([(100, 1.00, 1.40, 10, 10)],
                        columns=["strike", "bid", "ask", "volume", "openInterest"])
    less = pd.DataFrame([(100, 1.00, 1.24, 10, 10)],
                        columns=["strike", "bid", "ask", "volume", "openInterest"])
    two = {"expiries": [{"expiry": "2026-10-16", "dte": 35, "calls": wide, "puts": wide},
                        {"expiry": "2026-11-20", "dte": 70, "calls": less, "puts": less}]}
    r = _pick(two)
    assert "error" in r, f"both spreads are over the ceiling; expected rejection: {r}"
    t = r["diag"]["tightest"]
    assert t["expiry"] == "2026-11-20", (
        f"the tightest spread is in the SECOND expiry; reported {t['expiry']}. "
        f"Keeping the first match instead of the minimum would look correct on "
        f"a single-expiry chain and be wrong on every real one")
    # 1.00/1.24 -> spread 0.24, mid 1.12 -> 21.4%
    assert abs(t["spread_pct"] - 21.4) < 0.2, t
    assert abs(t["spread_pct"] - r["diag"]["best_spread_pct"]) < 0.2, (
        "the reported contract and the reported percentage must come from the "
        "same row, or the message contradicts itself")
    print(f"tightest contract : picked from expiry 2 at {t['spread_pct']}%, "
          f"strike {t['strike']:g}, mid {t['mid']:.2f}")

    # ── the message names it, says REJECTED, and states the overshoot ──
    msg = r["error"]
    assert "REJECTED" in msg, (
        "naming the tightest contract invites trading a contract that just "
        "failed the gate — the message must say so")
    for piece in ("CALL 100", "2026-11-20", "bid 1.00", "ask 1.24"):
        assert piece in msg, (piece, msg)
    assert f"{t['over_by_pct']:+g}" in msg, "the overshoot must be stated"
    assert abs(t["over_by_pct"] - (t["spread_pct"] -
               risk_params.MAX_OPTION_SPREAD_PCT)) < 1e-9
    print(f"message           : names it, marks REJECTED, "
          f"{t['over_by_pct']:+g} pts over the ceiling")

    # ── the ceiling is still ENFORCED, not merely described ──
    ok_row = _chain([(100, 1.00, 1.04, 50, 50)])       # spread 0.04 / mid 1.02 = 3.9%
    good = _pick(ok_row)
    assert "error" not in good, f"a 3.9% spread is inside the ceiling: {good}"
    assert good["label"] == "CALL" and good["strike"] == 100
    edge = _chain([(100, 1.00, 1.18, 50, 50)])         # 0.18 / 1.09 = 16.5%
    assert "error" in _pick(edge), "a 16.5% spread must still be refused"
    print(f"ceiling enforced  : 3.9% passes, 16.5% refused at the "
          f"{risk_params.MAX_OPTION_SPREAD_PCT:g}% gate")

    # ── tightest is only reported when something reached the spread test ──
    no_oi = _chain([(100, 1.00, 1.40, 10, 0)])
    r2 = _pick(no_oi)
    assert "error" in r2 and r2["diag"]["tightest"] is None, (
        "a contract that never passed open interest must not be offered as the "
        "tightest — it was never eligible")
    assert "open interest" in r2["error"]
    print("gating order      : no-OI strikes never surface as 'tightest'")

    # ── an inverted quote (ask < bid) is not a live quote ──
    inv = _pick(_chain([(100, 1.20, 1.00, 50, 50)]))
    assert "error" in inv and inv["diag"]["had_bid"] == 0, (
        "ask 1.00 under bid 1.20 is a crossed print with a negative spread; it "
        "must never count as quoted, let alone pass the ceiling", inv)
    print("inverted quote    : ask < bid is refused as unquoted")

    # ── PUT side reports PUT, not CALL ──
    bear = _pick(_chain([(100, 1.00, 1.40, 10, 10)]), trend="Bearish")
    assert bear["diag"]["tightest"]["right"] == "PUT", bear["diag"]["tightest"]
    print("side              : bearish rejection reports a PUT")

    # ── D2 (review 2026-11-03): NEAREST THE MONEY, NEVER BEYOND THE TARGET ──
    # 103 is by far the most liquid; the old score named it. 100 is the money.
    _d2 = _chain([(97, 4.00, 4.20, 50, 500), (100, 2.00, 2.10, 40, 300), (103, 0.90, 0.95, 900, 9000)])
    _p = _pick(_d2, atr=2.0)
    assert _p["strike"] == 100 and _p["moneyness_pct"] == 0.0, _p
    # the alert's target is 102.6: a liquid 103 is never named, even when 100 fails the gates
    _d2b = _chain([(100, 2.00, 2.60, 40, 300), (103, 0.90, 0.95, 900, 9000)])     # 100: 26 % spread
    _pb = get_option_data("TEST", price, "Bullish", "Normal", 20, 3.0, 1e9, _open, atr=2.0,
                          chain_data=_d2b, target=102.6)
    assert "error" in _pb and _pb["diag"]["window_hi"] == 102.6, ("a call above the target is never named", _pb)
    assert "error" not in get_option_data("TEST", price, "Bullish", "Normal", 20, 3.0, 1e9, _open, atr=2.0,
                                          chain_data=_d2b), "without a target the 103 is still in the band"
    # nothing inside 5 % of the money: refused, even with a wide ATR window (12 %)
    _far = _chain([(106, 1.00, 1.04, 900, 9000), (94, 7.00, 7.20, 900, 9000)])
    _pf = _pick(_far, atr=8.0)
    assert "error" in _pf and _pf["diag"]["window_lo"] == 95.0 and _pf["diag"]["window_hi"] == 105.0, _pf
    # the same strike on two expiries: liquidity breaks the tie
    _a = pd.DataFrame([(100, 2.00, 2.10, 10, 100)], columns=["strike", "bid", "ask", "volume", "openInterest"])
    _b = pd.DataFrame([(100, 2.50, 2.60, 500, 5000)], columns=["strike", "bid", "ask", "volume", "openInterest"])
    _tie = {"expiries": [{"expiry": "2026-11-27", "dte": 24, "calls": _a, "puts": _a},
                         {"expiry": "2026-12-04", "dte": 31, "calls": _b, "puts": _b}]}
    assert _pick(_tie, atr=2.0)["expiry"] == "2026-12-04"
    # THE THETA FLAG READS THE TIME-EXIT CLOCK (owner 2026-11-04: "go with 1-4"):
    # held until 7 DTE, a 24-DTE contract has ~12 sessions (a 4-ATR target's ~12),
    # a 23-DTE one ~11; it used to compare DTE itself and could never fail.
    assert [sessions_to_time_exit(d) for d in (7, 21, 23, 24, 30, 45)] == [0, 10, 11, 12, 16, 27]
    assert target_sessions_needed(4.0, 2.0) == 12 and target_sessions_needed(1.0, 2.0) == 5 \
        and target_sessions_needed(4.0, None) == 10
    _t24 = _pick(_tie, atr=2.0)                                         # 31 DTE wins the tie
    assert _t24["sessions_to_exit"] == 17 and _t24["dte_adequate"] is True, _t24
    _short = {"expiries": [{"expiry": "2026-11-25", "dte": 21, "calls": _a, "puts": _a}]}
    _s21 = get_option_data("TEST", price, "Bullish", "Normal", 20, 4.0, 1e9, _open, atr=2.0, chain_data=_short)
    assert _s21["days_needed"] == 12 and _s21["sessions_to_exit"] == 10 and _s21["dte_adequate"] is False, _s21
    print("theta flag       : sessions to the 7-DTE exit against the target's; 21-23 DTE flagged, 24+ not")
    # nearer the money wins across expiries, even when less liquid
    _c = pd.DataFrame([(101, 1.50, 1.55, 900, 9000)], columns=["strike", "bid", "ask", "volume", "openInterest"])
    _near = {"expiries": [{"expiry": "2026-11-27", "dte": 24, "calls": _c, "puts": _c},
                          {"expiry": "2026-12-04", "dte": 31, "calls": _a, "puts": _a}]}
    assert _pick(_near, atr=2.0)["strike"] == 100
    print("D2 strike         : nearest the money among liquid strikes, inside 5 %, never beyond the target; "
          "liquidity only breaks a tie")

    print("=" * 70)
    # ── DTE IS COUNTED FROM THE ET DATE (review 2026-11-03, N3) ──
    from datetime import datetime as _dtn
    from pathlib import Path as _P
    from zoneinfo import ZoneInfo as _Z
    assert et_today() == pd.Timestamp(_dtn.now(_Z("America/New_York")).date()), "et_today() is New York's date"
    for _f in ("option_chain.py", "scanner.py", "app.py"):
        _src = _P(__file__).with_name(_f).read_text(encoding="utf-8", errors="replace")
        assert ("Timestamp." + "today()") not in _src, f"{_f}: DTE must be counted from et_today(), not the host clock"
    print("DTE clock          : counted from the New York date in the selector, the scanner and the app (N3)")
    print("All self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(selftest())
