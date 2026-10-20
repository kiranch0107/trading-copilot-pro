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
import pathlib
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


# THE VERSION THIS SESSION LOADED, per file (second review 2026-10-20). An
# app-owned file (the journal, the skip log, the alert log) is written whole,
# so a second app session that loaded an older copy would overwrite the first
# session's rows. save_result() refuses that: a non-merged file is written
# only when the remote sha still equals the one this session loaded; a file
# loaded from the local fallback is "unknown" and is not overwritten at all
# until it is reloaded from the repo.
_LOADED_SHA: dict = {}
SHA_UNKNOWN = "unknown"


def _gh_put(path: str, data: list, message: str, remerge=None,
            expected_sha=None) -> str | None:
    """
    Write a JSON file to the repo. Returns an error string, or None on success.

    RE-MERGE ON CONFLICT (external review 2026-10-19, BACKLOG 35 group 4).
    A 409/412 means another writer landed between this call's GET and PUT.
    Retrying with the SAME payload against the fresh sha overwrote whatever
    that writer changed: the merge was done against the old remote. With
    `remerge(remote_data)` given, the payload is rebuilt from the remote as
    it now is before every retry; without it the retry is a plain overwrite,
    which is correct only for app-owned files nobody else writes.

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
            remote_now, sha, err = _gh_get(path)
            if err:
                return err
            # ONE SNAPSHOT FOR THE MERGE AND THE SHA, on EVERY attempt (second
            # review 2026-10-20). Re-merging only on a retry left the first
            # PUT carrying a merge against an older GET than the sha it sent:
            # a remote change between the two reads was overwritten without
            # any conflict being raised.
            if remerge is not None:
                data = remerge(remote_now if remote_now is not None else [])
            elif expected_sha is not None and sha != expected_sha:
                return (f"{path} changed in the repo since this session loaded it "
                        f"(loaded {str(expected_sha)[:7]}, now {str(sha)[:7]}); "
                        f"reload the page and apply the change again")
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
                try:
                    new_sha = (r.json() or {}).get("content", {}).get("sha")
                except Exception:                        # noqa: BLE001
                    new_sha = None
                if new_sha:
                    _LOADED_SHA[path] = new_sha
                else:
                    _LOADED_SHA.pop(path, None)
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


def load_jsonl(path: Path) -> tuple[list, str]:
    """
    Read-only: the current contents of a JSON-LINES file in the repo (the
    forward log), fresh from GitHub when a token is configured, else the
    local checkout. Returns (rows, source). The app never writes this file.
    """
    # EACH SOURCE IS PARSED INTO ITS OWN LIST and returned only whole
    # (external review 2026-10-19, N08). One list for both meant a remote
    # file with a valid row followed by a malformed one kept that row and
    # appended the local file to it: a mixture labelled "local", with
    # duplicate sequence numbers.
    if gh_enabled():
        try:
            r = requests.get(f"{_GH_API}/repos/{gh_repo()}/contents/{path.name}",
                             headers=_gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=10)
            if r.status_code == 200:
                raw = base64.b64decode(r.json().get("content", "")).decode("utf-8")
                remote: list = []
                for line in raw.splitlines():
                    line = line.strip()
                    if line:
                        remote.append(json.loads(line))
                return remote, "github"
        except Exception as e:                           # noqa: BLE001
            logger.warning("load_jsonl(%s): %s — falling back to local disk", path, e)
    try:
        local: list = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                local.append(json.loads(line))
        return local, "local (deploy-time copy)"
    except Exception:                                    # noqa: BLE001
        return [], "unavailable"


# Fields exit_monitor.py writes. On two records of the same id and rank, these
# come from the record the monitor touched more recently (last_check_epoch);
# everything else is app-owned and comes from local.
MONITOR_FIELDS = frozenset({"status", "exit_alerted", "exit_reason", "exit_detail",
                            "exit_premium", "exit_pnl_pct", "exit_detected",
                            "exit_history", "last_check_epoch"})


def _take_monitor_fields(lp: dict, rp: dict) -> dict:
    """lp with the monitor-owned fields from whichever record the monitor
    touched last. Equal or missing epochs keep lp as it is."""
    try:
        if float(rp.get("last_check_epoch") or 0) <= float(lp.get("last_check_epoch") or 0):
            return lp
    except (TypeError, ValueError):
        return lp
    out = dict(lp)
    for k in MONITOR_FIELDS:
        if k in rp:
            out[k] = rp[k]
    return out


def merge_positions(local: list | None, remote: list,
                    *, local_complete: bool = True, closed_ids=None) -> list:
    """
    Merge position lists by id, so the app and the monitor don't clobber
    each other.

    TWO RULES ADDED 2026-10-19 (external review, BACKLOG 35 group 4):

    - DELETIONS ARE TOMBSTONED BY THE JOURNAL. With `closed_ids` (the ids in
      the closed-trade journal) given, a remote-only id is dropped only when
      it is in that set: the app closed it. Any other remote-only id was ADDED
      by another writer (a second app session on another device) and is kept.
      Without `closed_ids` the old rule stands: absence from a complete local
      list is a deletion.
    - EQUAL RANK IS SETTLED BY THE MONITOR'S CLOCK. Two EXIT_SIGNALLED records
      used to resolve to local, so the app's stale THESIS could overwrite the
      monitor's later STOP. The monitor-owned fields (MONITOR_FIELDS) now come
      from the record with the larger last_check_epoch; app-owned fields stay
      local's.

    The monitor's job is to flip a position to EXIT_SIGNALLED. The app's job is
    to add new positions and remove closed ones. If both wrote at once, plain
    last-write-wins could silently discard an exit alert — the one piece of
    state you most need. So for any id present in BOTH, we keep the record that
    has progressed further (EXIT_SIGNALLED beats OPEN); ids only in local are
    additions/removals the app owns.

    `local_complete=False` (or local=None) means the local list is NOT a
    statement about what exists — it came from a read that failed. Absence then
    proves nothing, so no remote record is dropped.

    WHY THE FLAG EXISTS. "id missing from local" and "local could not be read"
    produce the same empty list, and the first legitimately means deletion — a
    user who closes their last position really does leave local empty. So the
    difference cannot be inferred here; it has to be carried from the read. With
    it inferred, an unreadable open_positions.json wiped every position in the
    repo.
    """
    rank = {"OPEN": 0, "EXIT_SIGNALLED": 1}
    if local is None or not local_complete:
        by_id = {p["id"]: p for p in (local or [])}
        for rp in remote:
            lp = by_id.get(rp["id"])
            if lp is None or rank.get(rp.get("status"), 0) > rank.get(lp.get("status"), 0):
                by_id[rp["id"]] = rp
        logger.warning(
            "merge: the local copy was unreadable, so %d remote position(s) "
            "are KEPT rather than treated as deletions. Fix or remove the local "
            "file; nothing has been lost.", len(remote))
        return list(by_id.values())

    by_id = {p["id"]: p for p in local}
    if closed_ids is not None:
        # A LOCAL record whose id the journal says is closed is a stale
        # session's copy (second review 2026-10-20); it must not resurrect
        # a position another session closed.
        by_id = {k: v for k, v in by_id.items() if k not in closed_ids}
    for rp in remote:
        lp = by_id.get(rp["id"])
        if lp is None:
            if closed_ids is not None and rp["id"] not in closed_ids:
                by_id[rp["id"]] = rp      # added elsewhere, not closed here: keep
            continue                       # closed by the app — respect that
        rr, lr = rank.get(rp.get("status"), 0), rank.get(lp.get("status"), 0)
        if rr > lr:
            by_id[rp["id"]] = rp
        elif rr == lr:
            by_id[rp["id"]] = _take_monitor_fields(lp, rp)
    return list(by_id.values())


def _local_load(path: Path) -> list | None:
    """
    Local copy, or None when the file exists but CANNOT BE READ.

    The None matters. This used to return [] on a parse failure, which is not
    "unknown" — it is the value that means "there are no positions". Combined
    with merge_positions(), which reads an id missing from local as "the app
    closed it", an unreadable open_positions.json erased every position in the
    repo, EXIT_SIGNALLED records included.

    Measured: 3 remote positions, local read fails -> [] -> merge returns 0 rows
    -> save() writes [] to the repo. Silent, and the one piece of state you
    cannot reconstruct.

    An ABSENT file still returns [] — that genuinely is "nothing here yet".
    """
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.exception("Failed to load %s: %s — treating as UNKNOWN, not as "
                         "empty, so a merge cannot mistake it for deletions",
                         path, e)
        return None


def _local_save(path: Path, data: list) -> bool:
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
        return True
    except Exception as e:
        logger.warning("Could not persist %s to disk (%s)", path, e)
        return False


# Files whose LOCAL copy could not be parsed this process. save(merge=True) must
# not read absence in such a list as a deletion — see merge_positions().
_LOCAL_READ_FAILED: set[str] = set()


def local_read_failed(path: Path) -> bool:
    """True if this file's local copy failed to parse during this process."""
    return path.name in _LOCAL_READ_FAILED


def load(path: Path) -> list:
    """
    Prefer the repo (shared, durable); fall back to local disk.

    Always returns a list so the UI can render. When the local fallback is
    UNREADABLE the emptiness is recorded as unknown rather than as fact, because
    the caller will hand this same list back to save() and an inferred deletion
    there wiped the repo copy.
    """
    if gh_enabled():
        data, _sha, err = _gh_get(path.name)
        if err:
            logger.warning("%s — falling back to local disk", err)
            st.session_state["_gh_last_error"] = err
            _LOADED_SHA[path.name] = SHA_UNKNOWN     # remote state not seen
        elif data is not None:
            _LOCAL_READ_FAILED.discard(path.name)
            _LOADED_SHA[path.name] = _sha
            return data
        else:
            _LOCAL_READ_FAILED.discard(path.name)
            _LOADED_SHA.pop(path.name, None)         # no file yet: first write is unconditional
            return []          # file not created yet
    local = _local_load(path)
    if local is None:
        _LOCAL_READ_FAILED.add(path.name)
        st.session_state["_gh_last_error"] = (
            f"{path.name} could not be read from local disk. It is being treated "
            f"as UNKNOWN, not as empty — existing records in the repo will be "
            f"preserved on the next save. Fix or delete the local file.")
        return []
    _LOCAL_READ_FAILED.discard(path.name)
    return local


def save_result(path: Path, data: list, *, merge: bool = False,
                closed_ids=None) -> tuple[list, str | None]:
    """
    save() with the outcome in the return value: (data_persisted, error).
    error is None when the durable write succeeded (or GitHub is off), else
    the message. Callers that must NOT proceed on a failed write (closing a
    position: the journal row has to land before the position is removed)
    read this instead of save()'s UI side effect (BACKLOG 35 group 4).
    """
    local_ok = _local_save(path, data)     # best-effort cache; wiped on container restart
    if not gh_enabled():
        # With GitHub off, local disk IS the store: a failed write is a failure.
        return data, (None if local_ok else f"could not write {path} on local disk")
    remerge = None
    final = {"data": data}
    expected = None
    if not merge:
        expected = _LOADED_SHA.get(path.name)
        if expected == SHA_UNKNOWN:
            return data, (f"{path.name} was loaded from the local fallback and the repo copy "
                          f"has not been seen; reload before saving so nothing in the "
                          f"repo is overwritten blind")
    if merge:
        incoming = list(data)
        complete = not local_read_failed(path)

        def _remerge(remote):
            merged = merge_positions(incoming, remote, local_complete=complete,
                                     closed_ids=closed_ids)
            final["data"] = merged          # what the PUT will carry, latest wins
            return merged
        remerge = _remerge
        remote, _sha, err = _gh_get(path.name)
        if not err and remote:
            data = remerge(remote)
            # RE-SAVE LOCALLY so the on-disk cache reflects the MERGED state
            # (see save() below for why).
            _local_save(path, data)
    err = _gh_put(path.name, data, f"chore: update {path.name} from app",
                  remerge=remerge, expected_sha=expected)
    if final["data"] is not data:
        # A conflict retry re-merged against a newer remote: the cache and
        # the caller get what was actually written, not the first merge.
        _local_save(path, final["data"])
    return final["data"], err


def save(path: Path, data: list, *, merge: bool = False, closed_ids=None) -> list:
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
    # The merge path RE-SAVES LOCALLY after merging so the on-disk cache
    # reflects the merged state: the first _local_save runs on the pre-merge
    # copy, and when the merge pulls in a change only the remote had (an
    # OPEN -> EXIT_SIGNALLED flip from exit_monitor.py) the cache would never
    # learn it; a later GitHub outage would then serve a position as open.
    # A sha conflict on the PUT re-merges against the remote as it then is.
    data, err = save_result(path, data, merge=merge, closed_ids=closed_ids)
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

        # ── GROUP 4 (2026-10-19): tombstones by the journal; the monitor's clock ──
        import json as _json, tempfile as _tmp
        # With closed_ids given, a remote-only id NOT in the journal was added
        # by another app session and survives; one in the journal was closed.
        _m2 = {p["id"]: p for p in merge_positions(local, remote, closed_ids={"C"})}
        assert "C" not in _m2, "a remote-only id in the journal is a close"
        _m3 = {p["id"]: p for p in merge_positions(local, remote, closed_ids=set())}
        assert "C" in _m3, "a remote-only id NOT in the journal was added elsewhere: keep it"
        # Equal rank: the app's stale THESIS must not overwrite the monitor's
        # later STOP; app-owned fields (notes) stay the app's.
        _lp = {"id": "A", "status": "EXIT_SIGNALLED", "exit_reason": "THESIS",
               "last_check_epoch": 100.0, "notes": "app edited"}
        _rp = {"id": "A", "status": "EXIT_SIGNALLED", "exit_reason": "STOP",
               "last_check_epoch": 200.0, "notes": "old"}
        _m4 = merge_positions([_lp], [_rp])[0]
        assert _m4["exit_reason"] == "STOP" and _m4["notes"] == "app edited", _m4
        _m5 = merge_positions([_rp], [_lp])[0]
        assert _m5["exit_reason"] == "STOP", "the older record never overwrites the newer"
        print("tombstones / clock      : journal ids are closes, other remote-only ids kept; "
              "equal rank takes the monitor's newer fields, keeps app fields")

        # ── A SHA CONFLICT RE-MERGES against the remote as it then is ──
        # GET 1: remote A OPEN. PUT 1: 409 (the monitor landed EXIT_SIGNALLED
        # meanwhile). GET 2: remote A EXIT_SIGNALLED. PUT 2 must carry
        # EXIT_SIGNALLED, not the first merge's OPEN.
        _bodies: list = []
        def _put_capture(seq):
            def _put(url, headers=None, json=None, timeout=None):
                calls["put"] += 1
                _bodies.append(json)
                return seq[min(calls["put"] - 1, len(seq) - 1)]
            return _put
        _get1 = _R(200, payload={"content": _b64.b64encode(
            _json.dumps([{"id": "A", "status": "OPEN", "last_check_epoch": 1.0}]).encode()).decode(), "sha": "s1"})
        _get2 = _R(200, payload={"content": _b64.b64encode(
            _json.dumps([{"id": "A", "status": "EXIT_SIGNALLED", "exit_reason": "STOP",
                          "last_check_epoch": 2.0}]).encode()).decode(), "sha": "s2"})
        with _tmp.TemporaryDirectory() as _d2:
            _p2 = pathlib.Path(_d2) / "open_positions.json"
            _p2.write_text("[]")
            calls["get"] = calls["put"] = 0
            _fk = _fake([_get1, _get2, _get2], [])
            _fk.put = _put_capture([_R(409, "conflict"), _R(201)])
            globals()["requests"] = _fk
            _ret, _err = save_result(_p2, [{"id": "A", "status": "OPEN", "notes": "n"}], merge=True)
            assert _err is None and calls["put"] == 2, (_err, calls)
            _sent2 = _json.loads(_b64.b64decode(_bodies[1]["content"]).decode())
            assert {p["id"]: p for p in _sent2}["A"]["status"] == "EXIT_SIGNALLED", (
                _sent2, "the retry re-put the stale merge; the monitor's exit flag would be lost")
            assert {p["id"]: p for p in _ret}["A"]["status"] == "EXIT_SIGNALLED", "the caller gets the re-merged state"
        print("conflict re-merge       : a 409 retry rebuilds the payload from the fresh remote")

        # ── save_result REPORTS A FAILED WRITE instead of only showing it ──
        calls["get"] = calls["put"] = 0
        globals()["requests"] = _fake([ok_get], [_R(401, "Bad credentials")])
        with _tmp.TemporaryDirectory() as _d3:
            _ret, _err = save_result(pathlib.Path(_d3) / "trade_journal.json", [{"id": "J"}])
            assert _err and "401" in _err, _err
        print("save_result             : a failed durable write is returned, not only displayed")

        # ── SECOND REVIEW 2026-10-20: the FIRST PUT merges against the sha it sends ──
        # GET 1 (save_result's pre-merge): A OPEN, sha s1. GET 2 (_gh_put's):
        # A EXIT_SIGNALLED, sha s2 — the monitor landed between the two reads.
        # The single PUT must carry EXIT_SIGNALLED; there is no conflict to
        # retry on, so the old code overwrote the exit flag.
        _bodies2: list = []
        def _put_cap2(seq):
            def _put(url, headers=None, json=None, timeout=None):
                calls["put"] += 1; _bodies2.append(json)
                return seq[min(calls["put"] - 1, len(seq) - 1)]
            return _put
        with _tmp.TemporaryDirectory() as _d4:
            _p4 = pathlib.Path(_d4) / "open_positions.json"; _p4.write_text("[]")
            calls["get"] = calls["put"] = 0
            _fk2 = _fake([_get1, _get2], []); _fk2.put = _put_cap2([_R(201)])
            globals()["requests"] = _fk2
            _ret4, _err4 = save_result(_p4, [{"id": "A", "status": "OPEN"}], merge=True)
            assert _err4 is None and calls["put"] == 1
            _sent4 = _json.loads(_b64.b64decode(_bodies2[0]["content"]).decode())
            assert {p["id"]: p for p in _sent4}["A"]["status"] == "EXIT_SIGNALLED", (
                _sent4, "the first PUT carried a merge against an older read than its sha")
        print("one snapshot            : the first PUT merges against the same read as its sha")

        # ── a stale LOCAL copy of a closed position does not resurrect it ──
        assert merge_positions([{"id": "A", "status": "OPEN"}], [], closed_ids={"A"}) == []
        assert merge_positions([{"id": "A", "status": "OPEN"}], []) != [], "without closed_ids the old rule stands"
        print("local tombstone         : a closed id is dropped from the local list too")

        # ── APP-OWNED FILES ARE WRITTEN ONLY AT THE VERSION THIS SESSION LOADED ──
        with _tmp.TemporaryDirectory() as _d5:
            _p5 = pathlib.Path(_d5) / "trade_journal.json"
            _LOADED_SHA["trade_journal.json"] = "s1"
            calls["get"] = calls["put"] = 0
            globals()["requests"] = _fake([_R(200, payload={"content": _b64.b64encode(b"[]").decode(), "sha": "s2"})], [_R(201)])
            _r5, _e5 = save_result(_p5, [{"id": "J"}])
            assert _e5 and "changed in the repo" in _e5 and calls["put"] == 0, (_e5, calls)
            calls["get"] = calls["put"] = 0
            globals()["requests"] = _fake([_R(200, payload={"content": _b64.b64encode(b"[]").decode(), "sha": "s1"})], [_R(201)])
            _r5, _e5 = save_result(_p5, [{"id": "J"}])
            assert _e5 is None and calls["put"] == 1, (_e5, calls)
            _LOADED_SHA["trade_journal.json"] = SHA_UNKNOWN
            calls["get"] = calls["put"] = 0
            _r5, _e5 = save_result(_p5, [{"id": "J"}])
            assert _e5 and "reload before saving" in _e5 and calls["put"] == 0, (_e5, calls)
            _LOADED_SHA.pop("trade_journal.json", None)
        print("conditional write       : a changed or unseen remote refuses the overwrite; a matching version writes")

        # ── GITHUB OFF: a failed local write is a failure ──
        globals()["_gh_token"] = lambda: None
        _r6, _e6 = save_result(pathlib.Path("/proc/no-such-dir/x.json"), [{"id": "L"}])
        assert _e6 and "local disk" in _e6, _e6
        globals()["_gh_token"] = lambda: "test-token"
        print("local backend           : an unwritable local store is reported, not success")

        # ── THE CACHE MUST HOLD THE MERGED STATE, NOT THE PRE-MERGE COPY ──
        # merge_positions() was tested in isolation; save() was not, and the
        # bug lived in save(). The local cache was written BEFORE the merge, so
        # a remote-only EXIT_SIGNALLED flip never reached disk. On a GitHub
        # outage the fallback then served a copy missing exactly the exit alert
        # the merge had just picked up, and the app showed the position open.
        import json as _json, tempfile as _tmp
        with _tmp.TemporaryDirectory() as _d:
            _path = pathlib.Path(_d) / "open_positions.json"
            _path.write_text(_json.dumps([{"id": "A", "status": "OPEN"}]))
            remote_payload = _json.dumps([{"id": "A", "status": "EXIT_SIGNALLED"}])
            merge_get = _R(200, payload={
                "content": _b64.b64encode(remote_payload.encode()).decode(),
                "sha": "sha-1"})
            calls["get"] = calls["put"] = 0
            globals()["requests"] = _fake([merge_get], [_R(201)])
            returned = save(_path, [{"id": "A", "status": "OPEN"}], merge=True)
            assert {p["id"]: p for p in returned}["A"]["status"] == "EXIT_SIGNALLED", \
                "save() must return the merged state"
            on_disk = {p["id"]: p for p in _json.loads(_path.read_text())}
            assert on_disk["A"]["status"] == "EXIT_SIGNALLED", (
                f"the ON-DISK cache still says {on_disk['A']['status']!r}. The "
                f"merge pulled in an exit flag and the cache never learned it; "
                f"a GitHub outage would now serve a position as still OPEN")
        print(f"cache after merge       : on-disk copy carries the merged "
              f"EXIT_SIGNALLED, not the stale OPEN")
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

    # ── an unreadable local copy must not read as deletions ──
    # Before this, _local_load() returned [] on a parse failure and
    # merge_positions() read every missing id as "the app closed it", so a
    # corrupt open_positions.json wiped every position in the repo —
    # EXIT_SIGNALLED records included. Measured: 3 remote, 0 survived.
    import tempfile as _tf
    _remote3 = [{"id": "A", "status": "OPEN"},
                {"id": "B", "status": "EXIT_SIGNALLED"},
                {"id": "C", "status": "OPEN"}]

    _d = Path(_tf.mkdtemp())
    (_d / "corrupt.json").write_text("{ not json")
    assert _local_load(_d / "corrupt.json") is None, \
        "an unreadable file must report UNKNOWN, not an empty list"
    assert _local_load(_d / "absent.json") == [], \
        "an absent file genuinely is empty and must stay []"
    (_d / "good.json").write_text(json.dumps(_remote3))
    assert len(_local_load(_d / "good.json")) == 3
    print("local load       : corrupt -> None, absent -> [], good -> rows")

    _wiped = merge_positions([], _remote3, local_complete=False)
    assert len(_wiped) == 3, (
        f"an unreadable local copy erased {3 - len(_wiped)} remote position(s); "
        f"absence in data that could not be read is not a deletion")
    assert merge_positions(None, _remote3) == merge_positions(
        [], _remote3, local_complete=False), \
        "local=None and local_complete=False must behave identically"
    # and the EXIT_SIGNALLED record specifically must survive
    assert any(p["id"] == "B" and p["status"] == "EXIT_SIGNALLED"
               for p in _wiped), "the exit alert is the one record you cannot " \
                                 "reconstruct; it must survive"
    print("unreadable merge : all 3 remote rows kept, exit alert intact")

    # LIVENESS: the legitimate deletion must still work, or the fix above is
    # just "never delete anything" wearing a flag.
    _closed = merge_positions([_remote3[0]], _remote3)
    assert [p["id"] for p in _closed] == ["A"], (
        f"a real close must still remove the position, got {_closed}")
    print("real deletion    : still honoured when the local copy was read")

    # ── load_jsonl NEVER MIXES SOURCES (external review 2026-10-19, N08) ──
    # A remote with one valid row then garbage falls back to the LOCAL file
    # alone; a whole remote is returned alone; no local file reads as
    # unavailable, not as the remote's good rows.
    import tempfile as _tf, base64 as _b64x
    _dir = Path(_tf.mkdtemp())
    _local = _dir / "fl.jsonl"
    _local.write_text('{"seq": 1, "src": "local"}\n{"seq": 2, "src": "local"}\n')
    _bad_remote = _R(200, payload={"content": _b64x.b64encode(
        b'{"seq": 1, "src": "remote"}\nnot json at all\n').decode()})
    _good_remote = _R(200, payload={"content": _b64x.b64encode(
        b'{"seq": 1, "src": "remote"}\n{"seq": 2, "src": "remote"}\n{"seq": 3, "src": "remote"}\n').decode()})
    globals()["_gh_token"] = lambda: "test-token"
    try:
        globals()["requests"] = _fake([_bad_remote], [])
        rows, src = load_jsonl(_local)
        assert src.startswith("local") and [r["src"] for r in rows] == ["local", "local"], (src, rows)
        assert [r["seq"] for r in rows] == [1, 2], "no duplicate sequence numbers from a mixture"
        globals()["requests"] = _fake([_good_remote], [])
        rows, src = load_jsonl(_local)
        assert src == "github" and len(rows) == 3 and all(r["src"] == "remote" for r in rows), (src, rows)
        globals()["requests"] = _fake([_bad_remote], [])
        rows, src = load_jsonl(_dir / "missing.jsonl")
        assert rows == [] and src == "unavailable", (rows, src)
    finally:
        globals()["requests"] = real_requests
        globals()["_gh_token"] = real_token
    print("load_jsonl        : a partial remote falls back to local alone; whole remote alone; missing -> unavailable")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
