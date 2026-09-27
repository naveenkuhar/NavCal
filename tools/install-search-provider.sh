#!/bin/sh
# Shows Navcal events in the GNOME Activities search.
# GNOME Shell only reads search providers from system folders, so this one
# file needs administrator rights. Log out and back in afterwards.
set -e
cd "$(dirname "$0")/.."
sudo install -Dm644 src/navcal/data/search-provider.ini \
    /usr/local/share/gnome-shell/search-providers/io.github.navcal.Navcal.search-provider.ini
echo "Installed. Log out and back in, then search for an event in Activities."
