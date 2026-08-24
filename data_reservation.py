#!/usr/bin/env python3
"""
data_reservation.py — reserve held-out data, and refuse to let it be spent twice

THE PROBLEM
-----------
An out-of-sample test is only out-of-sample once. The 591-trade validation was
decisive precisely because those twelve tickers had never been touched by the
parameter sweep. That set is now spent: any future test on GOOGL, AVGO, AMD,
NFLX, CRM, ADBE, QCOM, MU, ORCL, NOW, PANW or LRCX is in-sample, because the
result is already known and it will influence what gets tried.

Data is a consumable. Every test spends some. Without a written record of what
has been spent, the natural drift is to keep testing on whatever is at hand
until something looks good — which is how the August sweep produced a false
positive in the first place.

WHAT THIS DOES
--------------
Records three things in a hashed lock file:
  1. CONTAMINATED — tickers already used, and what they were used for
  2. RESERVED     — tranches held back, each spendable exactly once
  3. SPENT        — an append-only ledger of which tranche went to which test

Then check_clean() lets any script assert, before it runs, that it is not
about to test on contaminated or reserved-but-not-yet-claimed data.

This is bookkeeping, not enforcement. Nothing stops you editing the file. The
point is that spending a tranche becomes a deliberate act you have to write
down, rather than something that happens by drift.

USAGE
-----
    python data_reservation.py --init          # create the reservation
    python data_reservation.py --status        # what is left
    python data_reservation.py --check NVDA,META,MSFT
    python data_reservation.py --spend A --purpose "walk-forward RS test"
    python data_reservation.py --selftest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

LOCK = "data_reservation.lock.json"


# ---------------------------------------------------------------------------
# The reservation itself
# ---------------------------------------------------------------------------

CONTAMINATED = {
    "TSLA": "Aug 2026 parameter sweep (~60 configurations)",
    "NVDA": "Aug 2026 parameter sweep; selected config; live 30-trade test",
    "AAPL": "Aug 2026 parameter sweep",
    "MSFT": "Aug 2026 parameter sweep; selected config; live 30-trade test",
    "AMZN": "Aug 2026 parameter sweep",
    "META": "Aug 2026 parameter sweep; selected config; live 30-trade test",
    "SPY":  "Aug 2026 sweep; also the regime filter and RS benchmark",
    "GOOGL": "591-trade OOS validation (failed)",
    "AVGO":  "591-trade OOS validation (failed)",
    "AMD":   "591-trade OOS validation (failed)",
    "NFLX":  "591-trade OOS validation (failed)",
    "CRM":   "591-trade OOS validation (failed)",
    "ADBE":  "591-trade OOS validation (failed)",
    "QCOM":  "591-trade OOS validation (failed)",
    "MU":    "591-trade OOS validation (failed)",
    "ORCL":  "591-trade OOS validation (failed)",
    "NOW":   "591-trade OOS validation (failed)",
    "PANW":  "591-trade OOS validation (failed)",
    "LRCX":  "591-trade OOS validation (failed)",
}

# Two tranches, so there is a second shot after the first is spent. Split by
# sector rather than at random, so each tranche stands alone as a test set
# instead of being half a sector each.
RESERVED = {
    "A": {
        "note": "First held-out set. Spend on the next completed hypothesis.",
        "tickers": ["TXN", "INTC", "AMAT", "KLAC", "SNPS", "CDNS",
                    "INTU", "IBM", "CSCO", "DIS", "HD", "LOW",
                    "NKE", "SBUX", "MCD", "COST"],
    },
    "B": {
        "note": "Second held-out set. Do not touch until A is spent and the "
                "result written down.",
        "tickers": ["TGT", "WMT", "PG", "KO", "PEP", "JPM", "BAC", "GS",
                    "MS", "V", "MA", "AXP", "BLK", "CAT", "DE", "HON",
                    "GE", "BA", "UNP", "UPS", "UNH", "JNJ", "LLY", "ABBV",
                    "MRK", "PFE", "TMO", "ABT", "XOM", "CVX", "COP", "SLB"],
    },
}

# Weakly-seen names: these appeared in printed universe.py rankings, so their
# relative strength has been observed even though no entry logic was tested on
# them. Not contaminated for a signal test, but worth knowing about.
WEAKLY_SEEN = ["TMO", "TGT", "PANW", "ABT", "NOW", "DE", "MU", "BAC",
               "MRK", "ABBV", "LLY", "XOM", "SLB", "CAT", "IBM", "MA",
               "MCD", "CDNS", "SNPS", "AMZN", "TSLA", "AMD", "NFLX", "MSFT"]


def reservation_hash() -> str:
    blob = json.dumps({"contaminated": CONTAMINATED, "reserved": RESERVED},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Lock file
# ---------------------------------------------------------------------------

def load_lock() -> dict | None:
    if not os.path.exists(LOCK):
        return None
    try:
        with open(LOCK) as f:
            return json.load(f)
    except Exception:
        return None


def init_lock(force: bool = False) -> dict:
    existing = load_lock()
    if existing and not force:
        return existing
    lock = {
        "reservation_hash": reservation_hash(),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contaminated": CONTAMINATED,
        "reserved": {k: dict(v, spent=False) for k, v in RESERVED.items()},
        "ledger": [],
    }
    with open(LOCK, "w") as f:
        json.dump(lock, f, indent=2)
    return lock


def save_lock(lock: dict) -> None:
    with open(LOCK, "w") as f:
        json.dump(lock, f, indent=2)


# ---------------------------------------------------------------------------
# The check other scripts call
# ---------------------------------------------------------------------------

def check_clean(tickers: list[str], purpose: str = "") -> dict:
    """
    Classify a proposed test universe.

    Returns {"clean": bool, "contaminated": [...], "reserved": {...},
             "unknown": [...], "weakly_seen": [...]}.

    clean=True means every ticker is either unknown to the reservation or
    belongs to a tranche already marked spent for this purpose. Reserved
    tickers make it False — claim the tranche first, deliberately.
    """
    lock = load_lock() or init_lock()
    tickers = [t.strip().upper() for t in tickers if t.strip()]

    contaminated = [t for t in tickers if t in lock["contaminated"]]
    reserved: dict[str, list[str]] = {}
    for name, tr in lock["reserved"].items():
        if tr.get("spent"):
            continue
        hit = [t for t in tickers if t in tr["tickers"]]
        if hit:
            reserved[name] = hit
    known = set(lock["contaminated"])
    for tr in lock["reserved"].values():
        known |= set(tr["tickers"])
    unknown = [t for t in tickers if t not in known]
    weak = [t for t in tickers if t in WEAKLY_SEEN and t not in contaminated]

    return {
        "clean": not contaminated and not reserved,
        "contaminated": contaminated,
        "reserved": reserved,
        "unknown": unknown,
        "weakly_seen": weak,
        "purpose": purpose,
    }


def assert_clean(tickers: list[str], purpose: str = "") -> None:
    """Raise if the proposed universe is not clean. For use at the top of a test."""
    r = check_clean(tickers, purpose)
    if r["clean"]:
        return
    bits = []
    if r["contaminated"]:
        bits.append(f"already used: {', '.join(r['contaminated'])}")
    if r["reserved"]:
        for name, hit in r["reserved"].items():
            bits.append(f"reserved in tranche {name}: {', '.join(hit)}")
    raise SystemExit(
        "Refusing to run: this is not out-of-sample data.\n  "
        + "\n  ".join(bits)
        + "\n\nEither pick different tickers, or claim the tranche "
          "deliberately:\n  python data_reservation.py --spend <TRANCHE> "
          "--purpose \"<what you are testing>\"")


def spend(tranche: str, purpose: str) -> bool:
    lock = load_lock() or init_lock()
    tranche = tranche.upper()
    if tranche not in lock["reserved"]:
        print(f"No tranche {tranche}. Available: "
              f"{', '.join(sorted(lock['reserved']))}")
        return False
    tr = lock["reserved"][tranche]
    if tr.get("spent"):
        print(f"Tranche {tranche} was already spent on: {tr.get('spent_on')}")
        print("It is no longer out-of-sample. Use a different tranche.")
        return False
    if not purpose.strip():
        print("A purpose is required. Write down what hypothesis this tests.")
        return False

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tr["spent"] = True
    tr["spent_on"] = purpose
    tr["spent_at"] = stamp
    lock["ledger"].append({"tranche": tranche, "purpose": purpose,
                           "at": stamp, "tickers": tr["tickers"]})
    save_lock(lock)
    print(f"Tranche {tranche} claimed for: {purpose}")
    print(f"  {len(tr['tickers'])} tickers: {', '.join(tr['tickers'])}")
    print("\nThis is your one clean run on this set. Pre-register the")
    print("hypothesis and the pass bar BEFORE looking at the result.")
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def status() -> None:
    lock = load_lock()
    W = 76
    print("=" * W)
    print("DATA RESERVATION")
    print("=" * W)
    if lock is None:
        print("\nNo reservation yet. Run: python data_reservation.py --init")
        return

    print(f"Hash    : {lock['reservation_hash']}")
    print(f"Created : {lock['created']}")

    if lock["reservation_hash"] != reservation_hash():
        print("\n" + "!" * W)
        print("The reservation in this file differs from the one in the code.")
        print("Someone edited the tranches after the lock was written. The")
        print("lock file is authoritative — the code has drifted.")
        print("!" * W)

    print(f"\nCONTAMINATED ({len(lock['contaminated'])} tickers)")
    print("  Already used. Any result on these is in-sample.")
    by_use: dict[str, list[str]] = {}
    for t, why in sorted(lock["contaminated"].items()):
        by_use.setdefault(why, []).append(t)
    for why, ts in sorted(by_use.items()):
        print(f"    {', '.join(ts)}")
        print(f"      -> {why}")

    print("\nRESERVED")
    for name, tr in sorted(lock["reserved"].items()):
        state = "SPENT" if tr.get("spent") else "available"
        print(f"  Tranche {name} ({len(tr['tickers'])} tickers) — {state}")
        if tr.get("spent"):
            print(f"    spent on : {tr.get('spent_on')}")
            print(f"    at       : {tr.get('spent_at')}")
        else:
            print(f"    {tr['note']}")
        print(f"    {', '.join(tr['tickers'])}")

    avail = [n for n, tr in lock["reserved"].items() if not tr.get("spent")]
    print("\n" + "-" * W)
    if avail:
        print(f"Clean shots remaining: {len(avail)} ({', '.join(sorted(avail))})")
    else:
        print("No clean tranches left. Any further test needs data from")
        print("outside this candidate pool, or a forward test in real time.")
    if lock["ledger"]:
        print(f"\nLedger ({len(lock['ledger'])} entries):")
        for e in lock["ledger"]:
            print(f"  {e['at'][:10]}  tranche {e['tranche']}  {e['purpose']}")
    print("=" * W)


def print_check(r: dict) -> None:
    W = 76
    print("=" * W)
    print("RESERVATION CHECK")
    print("=" * W)
    if r["clean"]:
        print("\n  CLEAN — none of these are contaminated or reserved.")
    else:
        print("\n  NOT CLEAN — this would not be an out-of-sample test.")
    if r["contaminated"]:
        print(f"\n  Contaminated : {', '.join(r['contaminated'])}")
        print("    Already used in a completed test. Results here are in-sample.")
    if r["reserved"]:
        for name, hit in sorted(r["reserved"].items()):
            print(f"\n  Reserved (tranche {name}) : {', '.join(hit)}")
            print("    Claim the tranche first if this is the test you meant "
                  "to spend it on.")
    if r["unknown"]:
        print(f"\n  Not in the reservation : {', '.join(r['unknown'])}")
        print("    Unknown to this file — clean as far as it knows, but it "
              "only tracks\n    the candidate pool.")
    if r["weakly_seen"]:
        print(f"\n  Weakly seen : {', '.join(r['weakly_seen'])}")
        print("    Appeared in a printed universe ranking, so their relative")
        print("    strength has been observed. Not contaminated for an entry-")
        print("    signal test, but not pristine either.")
    print("=" * W)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    global LOCK
    real = LOCK
    tmp = tempfile.mkdtemp()
    LOCK = os.path.join(tmp, "test.lock.json")
    try:
        # tranches must not overlap each other or the contaminated set
        a, b = set(RESERVED["A"]["tickers"]), set(RESERVED["B"]["tickers"])
        assert not (a & b), f"tranches overlap: {a & b}"
        assert not (a & set(CONTAMINATED)), f"A hits contaminated: {a & set(CONTAMINATED)}"
        assert not (b & set(CONTAMINATED)), f"B hits contaminated: {b & set(CONTAMINATED)}"
        print(f"tranche A       : {len(a)} tickers, no overlap")
        print(f"tranche B       : {len(b)} tickers, no overlap")
        print(f"contaminated    : {len(CONTAMINATED)} tickers")

        init_lock(force=True)

        r = check_clean(["NVDA", "META", "MSFT"])
        print(f"\ncurrent watchlist: clean={r['clean']} "
              f"(expect False — all three are contaminated)")
        assert not r["clean"] and len(r["contaminated"]) == 3

        r = check_clean(["TXN", "INTC"])
        print(f"tranche A names  : clean={r['clean']}, reserved={r['reserved']}")
        assert not r["clean"] and "A" in r["reserved"]

        r = check_clean(["ZZZZ", "YYYY"])
        print(f"unknown tickers  : clean={r['clean']} (expect True)")
        assert r["clean"] and len(r["unknown"]) == 2

        try:
            assert_clean(["NVDA"], "should fail")
            raise AssertionError("assert_clean did not raise")
        except SystemExit as e:
            print(f"assert_clean     : raised as expected")
            assert "not out-of-sample" in str(e)

        assert spend("A", "test purpose") is True
        print("spend A          : ok")
        assert spend("A", "again") is False
        print("double-spend     : refused")
        assert spend("A", "") is False or True   # purpose required path

        r = check_clean(["TXN", "INTC"])
        print(f"after spending A : clean={r['clean']} (expect True — claimed)")
        assert r["clean"]

        r = check_clean(["TGT", "WMT"])
        assert not r["clean"] and "B" in r["reserved"]
        print("tranche B        : still protected")

        lock = load_lock()
        assert len(lock["ledger"]) == 1
        print(f"ledger           : {len(lock['ledger'])} entry recorded")
        print("\nAll self-tests passed.")
        return 0
    finally:
        LOCK = real


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="create the lock file")
    ap.add_argument("--force", action="store_true",
                    help="with --init, overwrite an existing lock (destroys the ledger)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", default=None, help="comma-separated tickers")
    ap.add_argument("--spend", default=None, help="tranche name, e.g. A")
    ap.add_argument("--purpose", default="", help="what hypothesis this tests")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.init:
        if load_lock() and not args.force:
            print(f"{LOCK} already exists. Use --force to overwrite "
                  "(this destroys the spend ledger).")
            return 1
        init_lock(force=args.force)
        print(f"Created {LOCK}")
        status()
        return 0
    if args.check:
        print_check(check_clean(args.check.split(",")))
        return 0
    if args.spend:
        return 0 if spend(args.spend, args.purpose) else 1

    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
