#!/bin/bash
# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later
# Runs the EDS integration tests against a private, throwaway calendar service.
# Your real calendars are never touched: D-Bus, config, data and cache are all
# isolated in a temporary directory.
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export XDG_CONFIG_HOME=$TMP/config XDG_DATA_HOME=$TMP/data XDG_CACHE_HOME=$TMP/cache
export NAVCAL_EDS_TEST=1 GDK_DEBUG=no-portals ADW_DISABLE_PORTAL=1 GTK_A11Y=none
exec dbus-run-session -- .venv/bin/python -m pytest -q tests/test_eds.py "$@"
