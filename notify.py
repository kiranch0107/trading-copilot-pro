#!/usr/bin/env python3
"""
notify.py — one Telegram sender, with retries

WHY THIS EXISTS
---------------
app.py, scanner.py and exit_monitor.py each had their own raw
requests.post(...) to api.telegram.org. Three copies of one thing, drifting:

    app.py           timeout=5,  no status check, exception swallowed
    scanner.py       timeout=10, status checked, no parse_mode
    exit_monitor.py  timeout=10, status checked, parse_mode=HTML

None of them retried. A single transient blip between a GitHub Actions runner
and Telegram silently dropped the message — and the messages this system sends
are exit alerts, which are the ones you cannot afford to miss. "It didn't
send" and "there was nothing to send" looked identical, which is the same
failure shape as the scan showing 0/0/0 during a Yahoo outage.

WHAT IT RETRIES, AND WHAT IT DOES NOT
--------------------------------------
Retried: connection errors, timeouts, and 429/500/502/503/504 — transient
conditions where the same request may well succeed a moment later.

NOT retried: 400 and 401. A malformed message or a bad token fails identically
every time; retrying turns a clear error into a slow clear error. 409 does not
occur on this endpoint.

Telegram's own 429 carries a retry_after; urllib3's backoff honours the
Retry-After header when present, so the schedule below is a floor, not an
override.

DEPENDENCY NOTE
---------------
Streamlit-free on purpose. scanner.py and exit_monitor.py import this and run
on GitHub Actions, which installs requests but not Streamlit.
"""
from __future__ import annotations

import logging
import os

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:                                    # very old urllib3
    Retry = None

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

RETRY_TOTAL = 3
RETRY_BACKOFF = 1.0          # 1s, 2s, 4s
RETRY_STATUSES = (429, 500, 502, 503, 504)
TIMEOUT_SEC = 15


def _session() -> requests.Session:
    s = requests.Session()
    if Retry is not None:
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=list(RETRY_STATUSES),
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def credentials() -> tuple[str | None, str | None]:
    return (os.environ.get("TELEGRAM_BOT_TOKEN"),
            os.environ.get("TELEGRAM_CHAT_ID"))


def send(message: str, *, parse_mode: str | None = None,
         dry_run: bool = False, session=None) -> bool:
    """
    Send one Telegram message. Returns True only if Telegram accepted it.

    Returning False rather than raising keeps the callers' existing control
    flow: a failed alert must never take down a scan that still has tickers
    left to examine.
    """
    token, chat_id = credentials()

    if dry_run:
        print("\n--- TELEGRAM (dry-run, not sent) ---")
        print(message)
        print("--- end ---\n")
        return True

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — no alert "
                     "sent. This is the most common reason no message arrives.")
        return False

    payload = {"chat_id": chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    sess = session or _session()
    try:
        r = sess.post(f"{TELEGRAM_API}/bot{token}/sendMessage",
                      data=payload, timeout=TIMEOUT_SEC)
    except Exception as e:
        # Retries are already exhausted by here; this is the final failure.
        logger.error("Telegram send failed after %d retries: %s",
                     RETRY_TOTAL, e)
        return False

    if r.status_code != 200:
        logger.error("Telegram API %s: %s", r.status_code, str(r.text)[:200])
        return False
    return True


# ---------------------------------------------------------------------------
# Self-test — no network
# ---------------------------------------------------------------------------

def selftest() -> int:
    class _Resp:
        def __init__(self, code, text="ok"):
            self.status_code, self.text = code, text

    class _FakeSession:
        def __init__(self, responses):
            self.responses, self.calls = list(responses), []

        def post(self, url, data=None, timeout=None):
            self.calls.append({"url": url, "data": data, "timeout": timeout})
            nxt = self.responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

    saved = {k: os.environ.get(k)
             for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    try:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)

        # Missing credentials must not send, and must not raise.
        s = _FakeSession([_Resp(200)])
        assert send("x", session=s) is False
        assert not s.calls, "no credentials must mean NO network call"
        print("no credentials          : returns False, makes no call")

        # dry-run must not call out even WITH credentials.
        os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
        os.environ["TELEGRAM_CHAT_ID"] = "chat"
        s = _FakeSession([_Resp(200)])
        assert send("x", dry_run=True, session=s) is True
        assert not s.calls, "dry-run must never reach the network"
        print("dry run                 : prints, sends nothing")

        s = _FakeSession([_Resp(200)])
        assert send("hello", session=s) is True
        assert s.calls[0]["data"]["chat_id"] == "chat"
        assert s.calls[0]["data"]["text"] == "hello"
        assert "parse_mode" not in s.calls[0]["data"], \
            "parse_mode must be omitted unless asked for — sending HTML mode " \
            "on a message with a stray < breaks the whole alert"
        print("healthy send            : posts once, no stray parse_mode")

        s = _FakeSession([_Resp(200)])
        assert send("<b>hi</b>", parse_mode="HTML", session=s) is True
        assert s.calls[0]["data"]["parse_mode"] == "HTML"
        print("parse_mode HTML         : passed through when requested")

        # A non-200 that urllib3 did not retry must be reported, not swallowed.
        s = _FakeSession([_Resp(400, "Bad Request: chat not found")])
        assert send("x", session=s) is False
        print("HTTP 400                : returns False (not retried, not hidden)")

        # A raised exception must be caught: one dead alert must never abort a
        # scan that still has tickers to examine.
        s = _FakeSession([requests.ConnectionError("boom")])
        assert send("x", session=s) is False
        print("connection error        : returns False, does not propagate")

        # The retry policy itself, on the real session.
        if Retry is not None:
            adapter = _session().get_adapter("https://api.telegram.org")
            r = adapter.max_retries
            assert r.total == RETRY_TOTAL, r.total
            assert 429 in r.status_forcelist and 503 in r.status_forcelist
            assert 400 not in r.status_forcelist, \
                "400 must NOT be retried — a malformed message fails the same " \
                "way every time and retrying only slows the report down"
            print(f"retry policy            : {r.total} attempts on "
                  f"{sorted(r.status_forcelist)}, never on 400/401")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
