# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Translations (gettext). Import `_` and `ngettext` from here.

Compiled translations live in navcal/locale/<lang>/LC_MESSAGES/navcal.mo, built
from po/*.po by tools/update-translations.sh (installed builds use the system's
locale folder instead). GtkBuilder files use the same
"navcal" domain, set in each .ui file.
"""

from __future__ import annotations

import gettext
import locale
import os
from importlib.resources import files

DOMAIN = "navcal"
# Installed builds set this (the launcher does); from the source folder, the package's own.
LOCALE_DIR = os.environ.get("NAVCAL_LOCALEDIR") or str(files("navcal").joinpath("locale"))

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    pass
gettext.bindtextdomain(DOMAIN, LOCALE_DIR)
try:  # for GtkBuilder, which translates with the C library's gettext
    locale.bindtextdomain(DOMAIN, LOCALE_DIR)
    locale.bind_textdomain_codeset(DOMAIN, "UTF-8")
except AttributeError:
    pass

_translation = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
pgettext = _translation.pgettext


def N_(message: str) -> str:
    """Mark a string for translation without translating it yet (translate on use)."""
    return message
