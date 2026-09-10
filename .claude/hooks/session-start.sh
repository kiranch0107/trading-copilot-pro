#!/bin/bash
# Claude Code on the web — make the project's own checks runnable.
# ─────────────────────────────────────────────────────────────────────────
# Without this, a web session starts with NONE of requirements.txt installed,
# so `python consistency_check.py` dies at the first import and every module
# --selftest is unavailable. The session can still read code, but it cannot
# run the thing that decides whether a change is safe — which is the one
# guard this project actually leans on.
#
# GitHub Actions is unaffected and needs no equivalent: tests.yml uses
# actions/setup-python, which ships an unpatched setuptools from PyPI.
set -euo pipefail

# Local CLI sessions use whatever environment the developer already has.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# ── Why setuptools is upgraded BEFORE requirements.txt ──────────────────
# ta==0.11.0 is sdist-only — PyPI ships no wheel for any version of it — so
# pip must run its setup.py. The container's system setuptools (68.1.2, at
# /usr/lib/python3/dist-packages) is the Debian-patched build: its
# command/install_lib.py does
#
#     self.set_undefined_options('install', ('install_layout', 'install_layout'))
#
# but the _distutils install command it bundles never defines install_layout,
# so the build dies with
#
#     AttributeError: install_layout. Did you mean: 'install_platlib'?
#
# That failure aborts the WHOLE `pip install -r requirements.txt`, which is
# why streamlit, pytz and altair also end up missing — they are collateral,
# not separate problems. A PyPI setuptools carries no such patch and builds
# ta cleanly. Installing it into dist-packages shadows the Debian copy for
# both isolated and non-isolated builds.
#
# This pins nothing in requirements.txt on purpose: the incompatibility is
# between ta's setup.py and ONE distro's setuptools packaging, not a property
# of this project's dependency set. Constraining setuptools for every
# consumer would be fixing CI and Streamlit Cloud for a problem neither has.
#
# setuptools ONLY — do not add `wheel` here. The Debian wheel 0.42.0 in
# dist-packages has no RECORD file, so pip refuses to replace it ("Cannot
# uninstall wheel 0.42.0") and the strict shell aborts the whole hook.
# setuptools >= 70 supplies its own bdist_wheel, so the wheel package is not
# needed for this build anyway.
python3 -m pip install --quiet --upgrade "setuptools>=70"

python3 -m pip install --quiet -r requirements.txt

# pyflakes is the linter tests.yml runs against app.py. It is deliberately
# not in requirements.txt (it is not a runtime dependency), so install it
# here or the lint step cannot be reproduced locally. Non-fatal: a missing
# linter should not stop a session from starting.
python3 -m pip install --quiet pyflakes || true

echo "deps installed — 'python consistency_check.py' and the --selftest flags are runnable"
