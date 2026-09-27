#!/bin/sh
# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later
# Builds the Flatpak and installs it for you, then makes navcal-<version>.flatpak:
# one file others can install with `flatpak install navcal-<version>.flatpak`.
# Needs, once: flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//50
set -e
cd "$(dirname "$0")/.."
APP_ID=io.github.navcal.Navcal
VERSION=$(sed -n "s/^  version: '\(.*\)',$/\1/p" meson.build)
flatpak run --no-documents-portal org.flatpak.Builder --user --install --force-clean \
    --state-dir=.flatpak/state --repo=.flatpak/repo .flatpak/build "build-aux/flatpak/$APP_ID.json"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    .flatpak/repo "navcal-$VERSION.flatpak" "$APP_ID"
echo "Installed for you. To share it: navcal-$VERSION.flatpak"
