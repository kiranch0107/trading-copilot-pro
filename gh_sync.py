#!/usr/bin/env python3
"""
gh_sync.py — GitHub-repo-backed JSON persistence for app.py

Extracted out of app.py, which had grown to ~3,450 lines mixing Streamlit UI,
this persistence layer, option-chain fetching and journal/position domain
logic in one file. This piece was the safest to pull out first: it has no
Streamlit UI of its own (no widgets, no layout), and signal_core.py already
established the pattern this repo uses for exactly this reason — a
self-contained module is easier to reason about and change than a slice of a
3,000-line file, and it can carry its own tests. This one doesn't have
selftest() because it makes 3 live GitHub API calls end to end; there's
nothing to meaningfully assert offline that isn't already covered by reading
the code. Correctness here is exercised the same way it always was: through
the app.

WHY THIS EXISTS — two failures it fixes:

1. DATA LOSS. st.session_state dies with the browser tab, and the local disk
   fallback is worthless on Streamlit Cloud because containers are stateless
   (wiped on redeploy, restart or idle timeout). Worse, an old version of
   this save path swallowed write failures into a log line nobody reads, so
   positions silently vanished with no error shown.

2. THE APP AND THE MONITOR COULDN'T SEE EACH OTHER. exit_monitor.py runs on
   GitHub Actions and reads open_positions.json from the REPO. The Streamlit
   app was writing to its own container filesystem. Two different disks that
   never sync — so positions logged in the app were invisible to the monitor,
   permanently, and no exit alert could ever have fired.

Making the repo the single source of truth solves both at once.

SETUP (one time):
  1. GitHub → Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token
       Repository access : only your trading-copilot-pro repo
       Permissions       : Repository permissions → Contents → Read and write
  2. Streamlit Cloud → your app → Settings → Secrets, paste:
       GITHUB_TOKEN = "github_pat_..."
     (Locally instead: export GITHUB_TOKEN=...)
  3. Commit an empty open_positions.json containing []  to the repo.

If no token is present the app degrades to local-disk-only and says so
loudly (via save(), below), rather than pretending to have saved.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path

import requests
import streamlit as st

logger = logging.getLogger(__name__)

# The repo this app persists into.
#
# The token is read from st.secrets FIRST and the environment second (see
# _gh_token). GITHUB_REPO was read from the environment ONLY — and Streamlit
# Cloud sets secrets, not environment variables — so a deployment configured
# the normal way silently fell through to the hardcoded default below. That
# default working by accident is why nobody noticed.
#
# Two consequences, both fixed here rather than by deleting the fallback:
#   1. It is now read from secrets first, so configuring it the same way as
#      the token actually takes effect.
#   2. A fork that sets neither would push its owner's positions into THIS
#      repo, or 403. The fallback is kept (removing it would break the running
#      deployment that depends on it) but it is now announced — gh_repo() logs
#      once, and repo_is_default() lets the UI say so.
_DEFAULT_REPO = "kiranch0107/trading-copilot-pro"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
_GH_API       = "https://api.github.com"
_warned_default_repo = False

# A 409/412 means another writer changed the file between our GET and our PUT.
# Retrying with a fresh sha is the correct response; there is nothing wrong
# with the request itself.
_PUT_MAX_ATTEMPTS = 3
_PUT_RETRY_STATUSES = (409, 412)
_PUT_BACKOFF_SEC = 0.5


def gh_repo() -> str:
    """Configured repo: Streamlit secrets, then environment, then the default."""
    global _warned_default_repo
    for src in ("secrets", "env"):
        try:
            val = (st.secrets.get("GITHUB_REPO") if src == "secrets"
                   else os.environ.get("GITHUB_REPO"))
        except Exception:
            val = None
        if val:
            return str(val).strip()
    if not _warned_default_repo:
        _warned_default_repo = True
        logger.warning(
            "GITHUB_REPO is not set in secrets or environment — falling back "
            "to %s. If this is a fork, your positions and journal are being "
            "written to someone else's repository (or failing with 403). Set "
            "GITHUB_REPO to your own repo.", _DEFAULT_REPO)
    return _DEFAULT_REPO


def repo_is_default() -> bool:
    """True when nothing was configured and the fallback is in use."""
    for getter in (lambda: st.secrets.get("GITHUB_REPO"),
                   lambda: os.environ.get("GITHUB_REPO")):
        try:
            if getter():
                return False
        except Exception:
            pass
    return True


# Kept as a module attribute because app.py displays it in two places. It is a
# snapshot of gh_repo() at import; every REQUEST calls gh_repo() directly so a
# secret added after start is picked up without a restart.
GITHUB_REPO   = gh_repo()


def _gh_token() -> str | None:
    """Token from Streamlit secrets first, then environment."""
    try:
        tok = st.secrets.get("GITHUB_TOKEN")
        if tok:
            return str(tok)
    except Exception:
        pass
    return os.environ.get("GITHUB_TOKEN")


def gh_enabled() -> bool:
    return bool(_gh_token())


def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {_gh_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_get(path: str) -> tuple[list | None, str | None, str | None]:
    """
    Fetch a JSON file from the repo.
    Returns (data, sha, error). A missing file is (None, None, None) — not an
    error, it just hasn't been created yet.
    """
    try:
        r = requests.get(
            f"{_GH_API}/repos/{gh_repo()}/contents/{path}",
            headers=_gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=10)
        if r.status_code == 404:
            return None, None, None
        if r.status_code != 200:
            return None, None, f"GitHub GET {r.status_code}: {r.text[:160]}"
        payload = r.json()
        raw = base64.b64decode(payload.get("content", "")).decode("utf-8") or "[]"
        return json.loads(raw), payload.get("sha"), None
    except Exception as e:
        return None, None, f"GitHub GET failed: {e}"


def _gh_put(path: str, data: list, message: str) -> str | None:
    """
    Write a JSON file to the repo. Returns an error string, or None on success.

    READ-MODIFY-WRITE: we always re-fetch the current sha immediately before
    writing. exit_monitor.py also writes this file (to mark exit_alerted), so a
    stale sha would be rejected with a 409.

    Re-fetching narrows that window to milliseconds but does not close it, and
    the previous version treated the 409 as fatal: it returned an error string
    that surfaced as an st.error() and the write was simply lost — a new
    position, or an exit flag, gone. "Rare" is not "handled", especially for
    the one piece of state you most need to survive.

    So a 409 (and a 412, which GitHub also uses for a stale sha) now re-reads
    the sha and tries again, with a short backoff. Any OTHER status fails
    immediately: a 401 or a 404 fails identically every time and retrying only
    turns a clear error into a slow one.
    """
    last = None
    for attempt in range(_PUT_MAX_ATTEMPTS):
        try:
            _, sha, err = _gh_get(path)
            if err:
                return err
            body = {
                "message": message,
                "content": base64.b64encode(
                    json.dumps(data, indent=2, default=str).encode("utf-8")).decode("utf-8"),
                "branch":  GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha
            r = requests.put(f"{_GH_API}/repos/{gh_repo()}/contents/{path}",
                             headers=_gh_headers(), json=body, timeout=10)
            if r.status_code in (200, 201):
                return None
            if r.status_code in _PUT_RETRY_STATUSES:
                last = f"GitHub PUT {r.status_code}: {r.text[:160]}"
                logger.warning("%s — sha conflict on %s, retry %d/%d",
                               r.status_code, path, attempt + 1,
                               _PUT_MAX_ATTEMPTS)
                time.sleep(_PUT_BACKOFF_SEC * (attempt + 1))
                continue
            return f"GitHub PUT {r.status_code}: {r.text[:160]}"
        except Exception as e:
            return f"GitHub PUT failed: {e}"
    return (f"GitHub PUT: {_PUT_MAX_ATTEMPTS} sha conflicts in a row on "
            f"{path} — another writer is holding it. Last: {last}")


def merge_positions(local: list, remote: list) -> list:
    """
    Merge position lists by id, so the app and the monitor don't clobber
    each other.

    The monitor's job is to flip a position to EXIT_SIGNALLED. The app's job is
    to add new positions and remove closed ones. If both wrote at once, plain
    last-write-wins could silently discard an exit alert — the one piece of
    state you most need. So for any id present in BOTH, we keep the record that
    has progressed further (EXIT_SIGNALLED beats OPEN); ids only in local are
    additions/removals the app owns.
    """
    rank = {"OPEN": 0, "EXIT_SIGNALLED": 1}
    by_id = {p["id"]: p for p in local}
    for rp in remote:
        lp = by_id.get(rp["id"])
        if lp is None:
            continue          # app deleted it (closed) — respect that
        if rank.get(rp.get("status"), 0) > rank.get(lp.get("status"), 0):
            by_id[rp["id"]] = rp
    return list(by_id.values())


def _local_load(path: Path) -> list:
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception as e:
        logger.exception("Failed to load %s: %s", path, e)
        return []


def _local_save(path: Path, data: list) -> bool:
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
        return True
    except Exception as e:
        logger.warning("Could not persist %s to disk (%s)", path, e)
        return False


def load(path: Path) -> list:
    """Prefer the repo (shared, durable); fall back to local disk."""
    if gh_enabled():
        data, _sha, err = _gh_get(path.name)
        if err:
            logger.warning("%s — falling back to local disk", err)
            st.session_state["_gh_last_error"] = err
        elif data is not None:
            return data
        else:
            return []          # file not created yet
    return _local_load(path)


def save(path: Path, data: list, *, merge: bool = False) -> list:
    """
    Write through to the repo AND local disk. Returns the data actually
    persisted — identical to `data` unless merge=True and a concurrent
    remote write was folded in (see merge_positions()).

    merge=True is for files another process can also write concurrently —
    in this app, only open_positions.json (exit_monitor.py flips OPEN ->
    EXIT_SIGNALLED on it independently). Re-fetches the remote copy
    immediately before writing so this save can never clobber an exit alert
    the monitor just wrote. Pass merge=True only for that file; every other
    file here is app-owned and a plain overwrite is correct.

    Unlike an older version, this does NOT fail silently. If the durable
    write fails the user is told in the UI, because "I logged a position and
    it vanished" is exactly the failure a silent warning produced.
    """
    _local_save(path, data)     # best-effort cache; wiped on container restart
    if not gh_enabled():
        st.session_state["_gh_last_error"] = None
        return data
    if merge:
        remote, _sha, err = _gh_get(path.name)
        if not err and remote:
            data = merge_positions(data, remote)
    err = _gh_put(path.name, data, f"chore: update {path.name} from app")
    st.session_state["_gh_last_error"] = err
    if err:
        st.error(f"⚠️ **Could not save to GitHub** — {err}\n\n"
                 f"Your change is only in this browser session and **will be "
                 f"lost** when you close the tab. The exit monitor also can't "
                 f"see it. Check your GITHUB_TOKEN secret.")
    return data


# ---------------------------------------------------------------------------
# Self-test — no network, no Streamlit runtime needed
# ---------------------------------------------------------------------------

def selftest() -> int:
    """
    Covers the write path, which had no test of any kind despite being where
    positions and the journal live. The 409 retry in particular: a lost write
    here means a position or an exit flag silently disappears.
    """
    import types

    class _R:
        def __init__(self, code, text="", payload=None):
            self.status_code, self.text = code, text
            self._payload = payload or {}
        def json(self):
            return self._payload

    real_requests, real_token = requests, _gh_token
    calls = {"get": 0, "put": 0}

    def _fake(get_seq, put_seq):
        def _get(url, headers=None, timeout=None, params=None):
            calls["get"] += 1
            return get_seq[min(calls["get"] - 1, len(get_seq) - 1)]
        def _put(url, headers=None, json=None, timeout=None):
            calls["put"] += 1
            return put_seq[min(calls["put"] - 1, len(put_seq) - 1)]
        return types.SimpleNamespace(get=_get, put=_put)

    globals()["_gh_token"] = lambda: "test-token"
    try:
        import base64 as _b64
        ok_get = _R(200, payload={"content": _b64.b64encode(b"[]").decode(),
                                  "sha": "sha-1"})

        # A clean write: one GET for the sha, one PUT.
        calls["get"] = calls["put"] = 0
        globals()["requests"] = _fake([ok_get], [_R(201)])
        assert _gh_put("x.json", [], "msg") is None
        assert calls["put"] == 1, calls
        print(f"clean write             : 1 GET + 1 PUT, no error")

        # THE FIX: a 409 must re-read the sha and try again, not lose the
        # write. The old code returned an error string here and the position
        # was gone.
        calls["get"] = calls["put"] = 0
        globals()["requests"] = _fake([ok_get], [_R(409, "conflict"), _R(201)])
        assert _gh_put("x.json", [], "msg") is None, \
            "a single sha conflict must be retried, not surfaced as a lost write"
        assert calls["put"] == 2, f"expected a retry, got {calls}"
        assert calls["get"] == 2, "the retry must re-read the sha, not reuse it"
        print(f"409 once                : retried with a fresh sha, write lands")

        # Persistent conflict still has to fail — loudly, and only after trying.
        calls["get"] = calls["put"] = 0
        globals()["requests"] = _fake([ok_get], [_R(409, "conflict")])
        err = _gh_put("x.json", [], "msg")
        assert err and "conflicts in a row" in err, err
        assert calls["put"] == _PUT_MAX_ATTEMPTS, calls
        print(f"409 always              : fails after {_PUT_MAX_ATTEMPTS} "
              f"attempts, says why")

        # A 401 is not a conflict. Retrying it just delays the real message.
        calls["get"] = calls["put"] = 0
        globals()["requests"] = _fake([ok_get], [_R(401, "Bad credentials")])
        err = _gh_put("x.json", [], "msg")
        assert err and "401" in err, err
        assert calls["put"] == 1, \
            "401 must NOT be retried — it fails the same way every time"
        print(f"401                     : fails immediately, not retried")

        # merge_positions: an exit flag must survive a concurrent app write.
        local = [{"id": "A", "status": "OPEN"}, {"id": "B", "status": "OPEN"}]
        remote = [{"id": "A", "status": "EXIT_SIGNALLED"},
                  {"id": "C", "status": "OPEN"}]
        merged = {p["id"]: p for p in merge_positions(local, remote)}
        assert merged["A"]["status"] == "EXIT_SIGNALLED", \
            "the monitor's exit flag must beat the app's stale OPEN"
        assert "B" in merged, "an app-only position must survive the merge"
        assert "C" not in merged, \
            "a remote id the app no longer has was CLOSED by the app; " \
            "resurrecting it would reopen a position the user closed"
        print(f"merge_positions         : EXIT_SIGNALLED beats OPEN, keeps "
              f"new, respects closes")
    finally:
        globals()["requests"] = real_requests
        globals()["_gh_token"] = real_token

    # M-6: the repo must be resolvable, and the fallback must announce itself.
    saved = os.environ.pop("GITHUB_REPO", None)
    try:
        assert repo_is_default() is True
        assert gh_repo() == _DEFAULT_REPO
        os.environ["GITHUB_REPO"] = "someone/else"
        assert gh_repo() == "someone/else", \
            "GITHUB_REPO from the environment must win over the fallback"
        assert repo_is_default() is False
    finally:
        os.environ.pop("GITHUB_REPO", None)
        if saved is not None:
            os.environ["GITHUB_REPO"] = saved
    print(f"repo resolution         : env overrides the fallback, flagged when not")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
