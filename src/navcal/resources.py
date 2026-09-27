"""Registers the compiled GResource bundle (UI files, CSS, icons).

Must be imported before any module that defines a Gtk.Template class.
Rebuild the bundle with tools/build-resources.sh.
"""

from importlib.resources import files

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

RESOURCE_BASE = "/io/github/navcal/Navcal"

Gio.Resource.load(str(files("navcal").joinpath("data/navcal.gresource")))._register()
Adw.init()  # template classes may use Adw widgets
