# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""The compiled GResource must be rebuilt after UI, CSS or icon changes."""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "navcal"


def test_gresource_is_up_to_date():
    manifest = (PKG / "navcal.gresource.xml").read_text()
    bundle = PKG / "data" / "navcal.gresource"
    assert bundle.exists(), "run tools/build-resources.sh"
    sources = [PKG / f for f in re.findall(r">([^<]+)</file>", manifest)] + [PKG / "navcal.gresource.xml"]
    stale = [str(s.relative_to(PKG)) for s in sources if s.stat().st_mtime > bundle.stat().st_mtime]
    assert not stale, f"run tools/build-resources.sh (changed: {', '.join(stale)})"


def test_ui_text_uses_typographic_characters():
    """HIG writing style: “…” not "...", ’ not ' in user-visible UI files."""
    for ui in (PKG / "ui").glob("*.ui"):
        for label in re.findall(r'translatable="yes">([^<]*)<', ui.read_text()):
            assert "..." not in label, f"{ui.name}: use … in {label!r}"
            assert "'" not in label, f"{ui.name}: use ’ in {label!r}"


def test_ui_text_is_translatable():
    """Every visible text property in the .ui files is marked for translation."""
    visible = ("label", "title", "subtitle", "description", "tooltip-text", "placeholder-text",
               "button-label", "text")
    for ui in (PKG / "ui").glob("*.ui"):
        text = ui.read_text()
        assert '<interface domain="navcal">' in text, ui.name
        for name, attrs, value in re.findall(r'<property name="([a-z-]+)"([^>]*)>([^<]*)<', text):
            if name in visible and value.strip() and value != "Navcal" and not value.startswith(("<", "&")):
                assert 'translatable="yes"' in attrs, f"{ui.name}: {name}={value!r}"
        for attr_name, attrs, value in re.findall(r'<attribute name="(label)"([^>]*)>([^<]*)<', text):
            assert 'translatable="yes"' in attrs, f"{ui.name}: menu {value!r}"


def test_translation_function_is_never_shadowed():
    """`_` is gettext; using it as a throwaway variable hides it (and crashes in
    any language but English)."""
    import ast
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports_gettext = any(isinstance(n, ast.ImportFrom) and any(a.name == "_" for a in n.names)
                              for n in ast.walk(tree))
        if not imports_gettext:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
                raise AssertionError(f"{path.name}:{node.lineno} assigns to _")
            if isinstance(node, ast.arg) and node.arg == "_":
                raise AssertionError(f"{path.name}:{node.lineno} uses _ as a parameter")
