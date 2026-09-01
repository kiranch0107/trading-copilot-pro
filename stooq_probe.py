#!/usr/bin/env python3
"""
stooq_probe.py — find a request shape Stooq will accept from this machine.

Run:  python stooq_probe.py
Then paste the output.

Stooq answers an UNKNOWN symbol with HTTP 200 and a short text body, so a 404
means the request itself was refused, not that the ticker is missing. The
usual culprits are the User-Agent (requests sends "python-requests/x.y", which
Stooq blocks) or the source IP (cloud/datacenter ranges are often refused).
This tries the combinations and reports which, if any, returns real CSV.
"""

import requests

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CASES = [
    ("stooq.com, default UA, params",
     "https://stooq.com/q/d/l/", {"s": "tgt.us", "i": "d"}, None),
    ("stooq.com, browser UA, params",
     "https://stooq.com/q/d/l/", {"s": "tgt.us", "i": "d"}, BROWSER_UA),
    ("stooq.com, browser UA, inline query",
     "https://stooq.com/q/d/l/?s=tgt.us&i=d", None, BROWSER_UA),
    ("stooq.pl,  browser UA, params",
     "https://stooq.pl/q/d/l/", {"s": "tgt.us", "i": "d"}, BROWSER_UA),
    ("stooq.com, browser UA, no trailing slash",
     "https://stooq.com/q/d/l", {"s": "tgt.us", "i": "d"}, BROWSER_UA),
]


def probe():
    winners = []
    for label, url, params, ua in CASES:
        headers = {"User-Agent": ua} if ua else {}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            body = (r.text or "")[:80].replace("\n", " | ")
            looks_csv = "Date" in r.text[:200] and "Open" in r.text[:200]
            rows = r.text.count("\n") - 1 if looks_csv else 0
            verdict = f"CSV, ~{rows} rows" if looks_csv else "not CSV"
            print(f"\n{label}")
            print(f"  HTTP {r.status_code}  -> {verdict}")
            print(f"  body: {body}")
            if looks_csv and rows > 10:
                winners.append(label)
        except Exception as e:
            print(f"\n{label}\n  EXCEPTION: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if winners:
        print("WORKING:")
        for w in winners:
            print(f"  - {w}")
    else:
        print("None worked. Stooq is likely refusing this IP rather than the")
        print("request shape — cloud/datacenter ranges are commonly blocked.")
        print("If so, Stooq is not a viable fallback from Streamlit Cloud or")
        print("GitHub Actions, and we should look at a keyed free API instead.")
    print("=" * 60)


if __name__ == "__main__":
    probe()
