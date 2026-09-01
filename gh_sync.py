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
from pathlib import Path

import requests
import streamlit as st

logger = logging.getLogger(__name__)

GITHUB_REPO   = os.environ.get("GITHUB_REPO", "kiranch0107/trading-copilot-pro")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
_GH_API       = "https://api.github.com"


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
            f"{_GH_API}/repos/{GITHUB_REPO}/contents/{path}",
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
    stale sha would be rejected with a 409. Re-fetching keeps the collision
    window to milliseconds.
    """
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
        r = requests.put(f"{_GH_API}/repos/{GITHUB_REPO}/contents/{path}",
                         headers=_gh_headers(), json=body, timeout=10)
        if r.status_code not in (200, 201):
            return f"GitHub PUT {r.status_code}: {r.text[:160]}"
        return None
    except Exception as e:
        return f"GitHub PUT failed: {e}"


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
