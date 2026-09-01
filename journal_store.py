#!/usr/bin/env python3
"""
journal_store.py — alert log, trade journal, open positions, position sizing

Extracted out of app.py — same reasoning as gh_sync.py before it (see that
module's docstring): app.py had grown to ~3,300 lines mixing Streamlit UI,
persistence, option-chain fetching and this domain logic together. This is
the second, larger piece pulled out; the option-chain-fetch functions are a
separate follow-up.

WHAT MOVED AND WHY THIS BOUNDARY
---------------------------------
Everything here either reads/writes one of the four JSON stores (alerts,
journal, positions, skipped signals) or computes on data already in hand
(journal_stats, calc_position_size). None of it needs live market data.

capture_entry_features() did NOT move here even though open_option_position()
uses it — it calls get_data()/compute()/drop_partial_bar()/get_spy_regime(),
which are app.py's market-data layer (a still-larger, still-coupled piece
left for its own follow-up). Moving it would have meant either a circular
import back into app.py or threading four callables through this module for
one caller. Instead open_option_position() now takes the already-computed
`entry_features` dict as a parameter: app.py calls
capture_entry_features(ticker) itself and passes the result in. Same
dependency-injection shape signal_core.py and data_source.py already use in
this repo, applied here at the one point where it was actually needed.

calc_position_size() likewise now takes `account_size` and `risk_pct` as
parameters instead of reading ACCOUNT_SIZE/RISK_PCT as module globals — those
two are Streamlit sidebar values read in many OTHER places in app.py too, so
they have to stay app.py globals; the function just stops assuming it can see
them by name. Same fix, same reason.

No selftest here, for the same reason gh_sync.py has none: every function
touches Streamlit session_state and/or gh_sync's live GitHub calls end to
end. Correctness is exercised through the app, same as before.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytz
import streamlit as st

import gh_sync

logger = logging.getLogger(__name__)

ALERT_LOG_FILE = Path("alert_history.json")
JOURNAL_FILE   = Path("trade_journal.json")
POSITIONS_FILE = Path("open_positions.json")
SKIPPED_FILE   = Path("skipped_signals.json")

_SS_ALERTS    = "_alerts_store"
_SS_JOURNAL   = "_journal_store"
_SS_POSITIONS = "_positions_store"
_SS_SKIPPED   = "_skipped_store"

# Below this many CLOSED journal trades, win rate / profit factor / total R
# are noise, not signal — informal floor for a single live log, distinct from
# oos_validate.py's pre-registered min_trades=60 (a formal significance test
# pooled across 12 tickers). Used by app.py's Performance Dashboard caveat.
MIN_JOURNAL_TRADES_FOR_SIGNAL = 30

# Alert cooldown per ticker. Was 600s (10 min) — but nothing scans this app
# every 10 minutes, so the cooldown never actually fired: a setup that stayed
# valid all session produced ~13 identical Telegram messages a day. The
# standalone scanner already uses 4 hours for exactly this reason; matching it
# keeps the two alert paths consistent.
COOLDOWN = 4 * 3600


# ─────────────────────────────────────────────
# PERSISTENCE
#
# BUG FIX #4: Streamlit Cloud containers are STATELESS. Files written to disk
# (alert_history.json / trade_journal.json) are destroyed on redeploy, restart,
# or idle timeout — silently wiping the user's entire trade journal.
#
# Mitigation (3 layers):
#   1. st.session_state is the primary read source (survives reruns instantly)
#   2. Disk is still written as a best-effort backup (works locally, and
#      survives short-lived reruns on cloud)
#   3. Export / Import buttons in the Journal tab so the user can persist
#      their data themselves — the only true fix on ephemeral hosting.
# ─────────────────────────────────────────────
def load_alerts() -> list:
    """Read from session_state first; hydrate from disk on first access."""
    if _SS_ALERTS not in st.session_state:
        st.session_state[_SS_ALERTS] = gh_sync.load(ALERT_LOG_FILE)
    return st.session_state[_SS_ALERTS]


def save_alerts(d: list) -> None:
    st.session_state[_SS_ALERTS] = d
    gh_sync.save(ALERT_LOG_FILE, d)


def load_journal() -> list:
    if _SS_JOURNAL not in st.session_state:
        st.session_state[_SS_JOURNAL] = gh_sync.load(JOURNAL_FILE)
    return st.session_state[_SS_JOURNAL]


def save_journal(d: list) -> None:
    st.session_state[_SS_JOURNAL] = d
    gh_sync.save(JOURNAL_FILE, d)


# ─────────────────────────────────────────────
# OPEN POSITIONS  (needed for exit monitoring)
#
# The journal only ever held CLOSED trades — add_journal_trade() requires
# exit_price and outcome. That meant the app had no idea what you were
# currently holding, so there was nothing for an exit monitor to watch.
# open_positions.json is the missing piece: it records a trade at ENTRY, and
# exit_monitor.py (run on a schedule) watches these for stop/target hits.
#
# Shape of a position record:
#   {id, ticker, trend, entry, stop, target, rr, opened, opened_epoch,
#    qty, instrument, notes, last_check_epoch, status}
# ─────────────────────────────────────────────
def load_positions() -> list:
    if _SS_POSITIONS not in st.session_state:
        st.session_state[_SS_POSITIONS] = gh_sync.load(POSITIONS_FILE)
    return st.session_state[_SS_POSITIONS]


def save_positions(d: list) -> None:
    # merge=True: exit_monitor.py also writes this file independently (to
    # flip OPEN -> EXIT_SIGNALLED). See gh_sync.save()'s docstring — this is
    # the one file in the app where a plain overwrite could discard an exit
    # alert the monitor just wrote.
    st.session_state[_SS_POSITIONS] = d
    st.session_state[_SS_POSITIONS] = gh_sync.save(POSITIONS_FILE, d, merge=True)


def load_skipped() -> list:
    if _SS_SKIPPED not in st.session_state:
        st.session_state[_SS_SKIPPED] = gh_sync.load(SKIPPED_FILE)
    return st.session_state[_SS_SKIPPED]


def save_skipped(d: list) -> None:
    st.session_state[_SS_SKIPPED] = d
    gh_sync.save(SKIPPED_FILE, d)


def log_skipped_signal(ticker: str, trend: str, reason: str,
                       notes: str = "", price: float = 0.0) -> None:
    """
    Record a signal you chose NOT to take.

    This is not bookkeeping for its own sake. Trades you skip are the control
    group: without them you only ever see the outcomes of decisions you made,
    which is how people conclude "my instincts are good" from a biased sample.
    Logging skips lets us later ask whether the ones you passed on would have
    done better or worse than the ones you took — the single most useful thing
    a 30-trade test can tell you about your own judgment.
    """
    skipped = load_skipped()
    skipped.append({
        "id":       f"SKIP_{ticker}_{int(time.time())}",
        "ticker":   ticker,
        "trend":    trend,
        "price":    round(float(price), 2) if price else None,
        "reason":   reason,
        "notes":    notes,
        "date":     datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d"),
        "logged":   datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
    })
    save_skipped(skipped)


def open_option_position(ticker: str, right: str, strike: float, expiry: str,
                         contracts: float, entry_premium: float,
                         rules: dict, notes: str = "",
                         entry_features: dict | None = None) -> dict:
    """
    Record an OPTION contract you bought so the exit monitor can watch it.

    Options need different exit logic than shares. A price stop on the
    underlying fits badly: an option can lose 40% of its value while the stock
    barely moves, purely from theta and IV. So each position carries its own
    `rules` block, and the monitor checks them in this priority:
        STOP   — premium fell to −sl_pct% of what you paid   (risk first)
        TARGET — premium rose to +tp_pct%
        TIME   — DTE at or below dte_exit (theta cliff)
        HOLD   — max_hold_bars trading sessions elapsed
        THESIS — underlying closed the wrong side of its EMA20
    Any rule set to 0 / False is disabled.

    `entry_features`: pass the result of app.py's capture_entry_features(ticker)
    (best-effort market-data snapshot). This module doesn't fetch it itself —
    see the module docstring for why. Pass None / omit if unavailable; it is
    stored as-is, never required.
    """
    positions = load_positions()
    now_epoch = time.time()
    pos = {
        "id":               f"OPT_{ticker}_{int(now_epoch)}",
        "ticker":           ticker,
        "right":            right.upper(),          # CALL | PUT
        "strike":           float(strike),
        "expiry":           expiry,                 # YYYY-MM-DD
        "contracts":        float(contracts),
        "entry_premium":    round(float(entry_premium), 2),
        "rules":            rules,
        "entry_features":   entry_features or {},
        "notes":            notes,
        "opened":           datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "opened_epoch":     now_epoch,
        "last_check_epoch": now_epoch,
        "status":           "OPEN",
        "exit_alerted":     False,
    }
    positions.append(pos)
    save_positions(positions)
    return pos


def close_position(position_id: str, exit_premium: float, outcome: str,
                   notes: str = "") -> None:
    """
    Move a position out of the open store and into the journal.

    For options the R multiple is measured in PREMIUM terms — (exit − entry) /
    entry — because that is what was actually at risk. On a long option your
    maximum loss is the premium paid, so a total loss is exactly −1.0R.
    """
    positions = load_positions()
    pos = next((p for p in positions if p["id"] == position_id), None)
    if pos is None:
        return

    if pos.get("right"):     # option position
        entry_prem = float(pos["entry_premium"])
        pnl_r = round((float(exit_premium) - entry_prem) / entry_prem, 2) \
            if entry_prem > 0 else 0
        journal = load_journal()
        journal = [j for j in journal if j["id"] != position_id]
        journal.append({
            "id":         position_id,
            "date":       pos["opened"],
            "closed":     datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
            "ticker":     f"{pos['ticker']} {pos['expiry']} {pos['strike']:g}{pos['right'][0]}",
            "trend":      "Bullish" if pos["right"] == "CALL" else "Bearish",
            "entry":      entry_prem,
            "stop":       0,
            "target":     0,
            "planned_rr": 0,
            "exit_price": round(float(exit_premium), 2),
            "outcome":    outcome,
            "actual_rr":  pnl_r,
            "contracts":  pos.get("contracts"),
            "pnl_usd":    round((float(exit_premium) - entry_prem) * 100
                                * float(pos.get("contracts") or 0), 2),
            "entry_features": pos.get("entry_features", {}),
            "notes":      notes or pos.get("notes", ""),
        })
        save_journal(journal)
    else:                     # legacy share position
        add_journal_trade(
            alert_id=pos["id"], ticker=pos["ticker"], trend=pos["trend"],
            entry=pos["entry"], stop=pos["stop"], target=pos["target"],
            rr=pos.get("rr", 0), exit_price=exit_premium, outcome=outcome,
            notes=notes or pos.get("notes", ""), setup_date=pos["opened"],
        )

    save_positions([p for p in positions if p["id"] != position_id])


def log_alert(ticker, trend, strength, entry, stop, target, rr, price,
              filters_passed: dict) -> None:
    alerts = load_alerts()
    now_epoch = time.time()

    # BUG FIX #2: cooldown was comparing against strptime("... ET") which
    # produces a NAIVE datetime — the literal "ET" is not parsed as a timezone.
    # .timestamp() then interpreted it in the SERVER's local tz (UTC on
    # Streamlit Cloud), a 4-5 hour offset, so the cooldown never triggered and
    # duplicate Telegram alerts fired on every scan.
    # Fix: store a real epoch alongside the display string and compare on that.
    recent = [a for a in alerts if a["ticker"] == ticker]
    if recent:
        last = recent[-1]
        last_epoch = last.get("epoch")
        if last_epoch is None:
            # Legacy record without epoch — fall back to a tz-aware parse
            try:
                naive = datetime.strptime(last["timestamp"], "%Y-%m-%d %H:%M ET")
                aware = pytz.timezone("America/New_York").localize(naive)
                last_epoch = aware.timestamp()
            except Exception:
                last_epoch = 0
        if now_epoch - float(last_epoch) < COOLDOWN:
            logger.info("Cooldown active for %s — alert suppressed", ticker)
            # BUG FIX: this used to return None, indistinguishable from a
            # successful write. The caller then sent a Telegram message anyway,
            # so the cooldown suppressed the LOG entry but not the alert — the
            # duplicate-notification problem it was built to prevent. Callers
            # must branch on this boolean.
            return False

    alerts.append({
        "id":             f"{ticker}_{int(now_epoch)}",
        "timestamp":      datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "epoch":          now_epoch,   # tz-safe cooldown source of truth
        "ticker":  ticker, "trend":    trend,    "strength": strength,
        "price":   price,  "entry":    entry,    "stop":     stop,
        "target":  target, "rr":       rr,
        "filters_passed": filters_passed, "journaled": False,
    })
    save_alerts(alerts)
    return True


def add_journal_trade(alert_id, ticker, trend, entry, stop, target,
                      rr, exit_price, outcome, notes, setup_date) -> None:
    journal = load_journal()
    risk    = abs(entry - stop)
    pnl_r   = round((exit_price - entry) / risk, 2) if trend == "Bullish" \
              else round((entry - exit_price) / risk, 2)
    journal = [j for j in journal if j["id"] != alert_id]
    journal.append({
        "id": alert_id, "date": setup_date,
        "closed": datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
        "ticker": ticker, "trend": trend, "entry": entry, "stop": stop, "target": target,
        "planned_rr": rr, "exit_price": exit_price,
        "outcome": outcome, "actual_rr": pnl_r, "notes": notes,
    })
    save_journal(journal)
    alerts = load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a["journaled"] = True
    save_alerts(alerts)


def journal_stats(journal: list) -> dict:
    if not journal:
        return {}
    # Cross-app safety: the Restore uploader accepts backups from the
    # discipline-enforcer app, whose journal contains OPEN trades without
    # "closed"/"exit_price" keys. Only closed outcomes count here.
    _n_open = sum(1 for j in journal if j.get("outcome") == "OPEN")
    journal = [j for j in journal if j.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
    if not journal:
        return {"open": _n_open} if _n_open else {}
    wins   = [j for j in journal if j["outcome"] == "WIN"]
    losses = [j for j in journal if j["outcome"] == "LOSS"]
    be     = [j for j in journal if j["outcome"] == "BREAKEVEN"]
    total  = len(journal)
    wr     = round(len(wins)/total*100, 1)
    avg_win  = round(sum(j["actual_rr"] for j in wins)  /len(wins),  2) if wins   else 0
    avg_loss = round(sum(j["actual_rr"] for j in losses)/len(losses), 2) if losses else 0
    total_r  = round(sum(j["actual_rr"] for j in journal), 2)
    gp = sum(j["actual_rr"] for j in wins   if j["actual_rr"] > 0.05)   # J1 FIX: ignore dust trades
    gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < -0.05))  # same floor on loss side
    # If all wins/losses are below 0.05R, fall back to full set so pf isn't 0/inf
    if gp == 0: gp = sum(j["actual_rr"] for j in wins if j["actual_rr"] > 0)
    if gl == 0: gl = abs(sum(j["actual_rr"] for j in losses if j["actual_rr"] < 0))
    pf = round(gp/gl, 2) if gl else float("inf")
    outcomes    = [j["outcome"] for j in sorted(journal, key=lambda x: x.get("closed", ""))]
    streak      = 0
    streak_type = outcomes[-1] if outcomes else ""
    for o in reversed(outcomes):
        if o == streak_type: streak += 1
        else: break
    # FIX #5: build equity curve for chart
    sorted_j = sorted(journal, key=lambda x: x.get("closed", ""))
    cum_r    = 0.0
    eq_curve = []
    for j in sorted_j:
        cum_r += j["actual_rr"]
        eq_curve.append({"date": j["closed"][:10], "Cumulative R": round(cum_r, 2)})
    return {
        "total": total, "open": _n_open,
        "wins": len(wins), "losses": len(losses), "breakeven": len(be),
        "win_rate": wr, "avg_win_r": avg_win, "avg_loss_r": avg_loss,
        "total_r": total_r, "profit_factor": pf, "streak": streak,
        "streak_type": streak_type, "equity_curve": eq_curve,
    }


# ─────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────
def calc_position_size(entry: float, stop: float, account_size: float,
                       risk_pct: float, option_premium: float | None = None,
                       shares_per_contract: int = 100) -> dict:
    """
    Position sizing for SHARES and (optionally) for a DEBIT OPTION.

    `account_size` / `risk_pct` are passed in rather than read as globals:
    they're Streamlit sidebar values app.py also displays directly in many
    other places, so they have to live there as ACCOUNT_SIZE/RISK_PCT; this
    function just stops assuming it can see them by name now that it lives
    in a different module. Same value, explicit instead of implicit.

    BUG FIX #3 (earlier): `contracts = max(1, shares // 100)` floored to 1
    contract even when the risk budget afforded none — silently blowing through
    the configured risk limit by up to 33x. The floor is gone.

    BUG FIX (this round) — OPTION RISK WAS MIS-MODELLED:
    The previous version computed
        risk_per_contract = 100 × (entry − stop)
    i.e. the risk of 100 SHARES of stock. That is not what a long option risks.
    Every recommendation this app makes is a DEBIT position (buying a CALL or a
    PUT), and for a debit position **maximum loss = the premium paid**. You
    cannot lose $830 on a call that cost $200.

    Consequences of the old model, measured on this watchlist:
      • overstated option risk ~4-5x (AAPL 333.26/324.96 → claimed $830/contract
        when a $2.00 call actually risks $200)
      • therefore returned 0 contracts for EVERY realistic setup — to get 1 you
        needed a sub-$14.25 stock with a sub-$0.15 stop
      • printed a nonsense "you'd need ~$83,000" account requirement

    Now: when the option premium is known we size the option on its true cost,
    capped by BOTH the risk budget and buying power. The stock-stop distance
    still drives SHARE sizing, where it is the correct measure.

    `option_premium` is per-share (i.e. the quoted mid); one contract costs
    premium × 100. Pass None when no chain data is available — the function
    then reports shares only and says so rather than inventing a contract count.
    """
    risk_dollars = round(account_size * risk_pct / 100, 2)
    per_share    = abs(entry - stop)

    if per_share <= 0:
        return {
            "risk_dollars": risk_dollars, "shares": 0, "contracts": 0,
            "affordable": False, "option_known": False,
            "note": "Invalid stop (zero risk per share).",
        }

    # ── SHARE sizing — stop distance is the right risk measure here ──
    shares_by_risk = int(risk_dollars / per_share)

    # NOTIONAL CAP: risk-based sizing alone can suggest more stock than the
    # account can buy. A $1 stock with a $0.01 stop → 1,500 shares = 100% of a
    # $1,500 account. Cap by buying power (95%, leaving room for fees).
    shares_by_cash  = int((account_size * 0.95) / entry) if entry > 0 else 0
    shares          = min(shares_by_risk, shares_by_cash)
    notional_capped = shares_by_cash < shares_by_risk

    result = {
        "risk_dollars": risk_dollars,
        "shares":       shares,
        "per_share":    round(per_share, 2),
    }

    # ── OPTION sizing — premium IS the risk on a debit position ──
    if option_premium is None or option_premium <= 0:
        # No chain data. Report shares only; do NOT fabricate a contract count.
        result.update({
            "contracts": None, "option_known": False, "affordable": False,
            "note": ("Option premium unknown (no chain data) — share sizing "
                     "shown. Open the 🧠 Options tab to size the contract."),
        })
    else:
        cost_per_contract  = round(option_premium * shares_per_contract, 2)
        contracts_by_risk  = int(risk_dollars / cost_per_contract)
        contracts_by_cash  = int((account_size * 0.95) / cost_per_contract)
        contracts          = min(contracts_by_risk, contracts_by_cash)
        # Largest premium that fits the risk rule, in per-share terms
        max_premium        = round(risk_dollars / shares_per_contract, 2)

        result.update({
            "contracts":         contracts,
            "option_known":      True,
            "cost_per_contract": cost_per_contract,
            "max_premium":       max_premium,
            "affordable":        contracts >= 1,
        })

        if contracts >= 1:
            result["note"] = None
        else:
            pct_of_acct = cost_per_contract / account_size * 100
            result["note"] = (
                f"1 contract costs **\\${cost_per_contract:,.2f}** "
                f"(\\${option_premium:,.2f} × {shares_per_contract}) — that's the "
                f"**maximum you can lose** on a long option, and it's "
                f"**{pct_of_acct:.1f}%** of your \\${account_size:,} account "
                f"(your limit: {risk_pct}% = \\${risk_dollars:,.2f}). "
                f"Within your rule you could afford a contract priced up to "
                f"**\\${max_premium:,.2f}**. Alternatives: trade "
                f"**{shares} share(s)** instead, raise **Risk per trade**, or "
                f"look for a cheaper contract."
            )

    if notional_capped:
        cap_note = (
            f"⚠️ Share count limited by buying power: risk sizing suggested "
            f"{shares_by_risk:,} shares but \\${account_size:,} only covers "
            f"{shares_by_cash:,} at \\${entry:,.2f}/share."
        )
        result["note"] = f"{cap_note}\n\n{result['note']}" if result.get("note") else cap_note

    return result
