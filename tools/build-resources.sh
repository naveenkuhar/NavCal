#!/bin/sh
# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later
# Compiles UI files, CSS and icons into src/navcal/data/navcal.gresource.
# Run after changing anything listed in src/navcal/navcal.gresource.xml.
set -e
cd "$(dirname "$0")/../src/navcal"
glib-compile-resources --target=data/navcal.gresource navcal.gresource.xml
echo "Built src/navcal/data/navcal.gresource"
