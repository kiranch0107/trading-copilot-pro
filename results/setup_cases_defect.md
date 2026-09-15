# Stage 1 run 1 is VOID — the readings never reached the setup

## What was printed

`results/setup_cases_run1.txt` (commit `2a5b6f6`, since removed — recover it
with `git show 2a5b6f6:results/setup_cases_run1.txt`) contained 80 real trades
across 20 tickers. Every one of them rendered like this:

        price      0.00   EMA20      0.00   EMA50      0.00
        price is +0.00 ATR from EMA20; EMA20 is +0.00 ATR from EMA50
        ATR      4.21  = nan% of price
      MOMENTUM
        RSI   0.0        ADX   0.0
      PARTICIPATION
        volume  0.00x its 20-day average
      GATES  (?/? passed)

and the summary table read:

    reading                winners    losers       gap
    planned R:R               2.85      2.49     +0.36
    RSI                       0.00      0.00     +0.00
    ADX                       0.00      0.00     +0.00
    ATR %                      nan       nan      +nan
    volume x                  0.00      0.00     +0.00
    price-EMA20 ATR           0.00      0.00     +0.00
    EMA20-EMA50 ATR           0.00      0.00     +0.00
    stop %                    3.25      2.73     +0.52
    gates passed              0.00      0.00     +0.00

The trades are real. The R-multiples, hold times and dates are real. Only two
readings were real — planned R:R and stop %, and those are computed from entry
and stop, which are the only inputs that survived.

## The defect

`backtest.evaluate_signal()` called `signal_core.evaluate()`, which returns
seventeen keys including every indicator reading, and then returned six of
them:

```python
return {"trend": r["trend"], "entry": r["entry"], "stop": r["stop"],
        "target": r["target"], "rr": r["rr"], "atr": r["atr"]}
```

`simulate_trade()` stored that six-key dict as the trade's `setup`, under a
comment I wrote claiming it carried "every gate, every indicator reading".
`setup_cases.py` then read `s.get("rsi")` on a dict with no `rsi` key and
rendered the default: `0.0`. Not an error — a zero. Eighty of them.

## Why the guard did not catch it

`setup_cases.selftest()` had a wiring guard. It did this:

```python
ev = inspect.getsource(signal_core.evaluate)
for key in ("high_quality", "filters", "vol_ratio", "strength"):
    assert f'"{key}"' in ev
```

It read the source of the **producer** and confirmed the key names appeared in
it. They did, and they still do — `signal_core.evaluate()` was never broken.
The break was in the handoff, one function downstream, and a guard pointed at
the producer cannot see it by construction. It stayed green through the
entire run.

The fixtures did not help either: `mk()` fabricates a setup dict with every
field populated, so the rendering and the derived-units tests ran on data that
had never been through the real pipe.

## The fix

`evaluate_signal()` now passes the whole evaluation through, minus `blocked`
(always False by that point) and `ticker` (the literal `"BT"` it hands to
`sc.evaluate`, not the ticker being traded). A whitelist is the wrong shape:
every field `signal_core` gains would have to be remembered in a second place
or vanish silently, and the failure shows up as a zero rather than an error.

Both suites now assert on **the value that arrives**, not on source text.
`backtest.selftest()` checks the returned dict carries the readings and that
they are real (`0 < rsi < 100`, `price > 0`, `atr > 0`, `filters` populated).
`setup_cases.selftest()` runs a real signal through `evaluate_signal()`, feeds
the result to its own `derived()`, and asserts every rendered reading is
finite and non-zero.

## Falsified

Seven breaks, each confirmed to turn a suite red:

| break | caught by |
|---|---|
| the original six-key projection restored | backtest, setup_cases |
| only `rsi`/`adx` dropped | backtest, setup_cases |
| only `ema20`/`ema50` dropped | backtest, setup_cases |
| `atr` dropped (every ATR-unit reading → nan) | backtest, setup_cases |
| `filters` dropped (gate detail lost) | backtest, setup_cases |
| `price` dropped (ATR% → nan) | backtest, setup_cases |
| `simulate_trade` stops carrying the setup | setup_cases |

## Standing lesson

Two guards in two consecutive pieces of work have now checked the wrong end of
a pipe — this one, and `data_reservation`'s hash-refresh assertion, which
passed with the refresh deleted because the test lock's hash was already
current. Both were green. Both were measuring something that could not fail.

A guard that reads source text is testing that a string exists. A guard worth
having consumes the output the production path actually produces.

## Status

Stage 1 must be re-run. No conclusion was drawn from run 1 and none could have
been — there was nothing in it to draw from.
