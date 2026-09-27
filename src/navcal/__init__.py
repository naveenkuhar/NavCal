"""Navcal: a GNOME calendar with reminders."""

VERSION = "0.6.0"


def main() -> int:
    import locale

    locale.setlocale(locale.LC_ALL, "")
    from .application import main as run

    return run()


__all__ = ["VERSION", "main"]
