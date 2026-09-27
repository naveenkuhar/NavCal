#!/bin/sh
# Extracts translatable text into po/navcal.pot, merges it into po/<lang>.po,
# and compiles those into src/navcal/locale/<lang>/LC_MESSAGES/navcal.mo.
# Run after changing any user-visible text. Add languages to po/LINGUAS.
set -e
cd "$(dirname "$0")/.."
PY=$(find src/navcal -name '*.py' | sort)
UI=$(find src/navcal/ui -name '*.ui' | sort)
xgettext --from-code=UTF-8 --package-name=Navcal --add-comments=Translators \
    --keyword=_ --keyword=N_ --keyword=ngettext:1,2 --keyword=pgettext:1c,2 \
    -L Python -o po/navcal.pot $PY
xgettext --from-code=UTF-8 --join-existing -o po/navcal.pot $UI
for lang in $(cat po/LINGUAS); do
    if [ -f "po/$lang.po" ]; then
        msgmerge --quiet --update --backup=none "po/$lang.po" po/navcal.pot
    else
        msginit --no-translator --locale="$lang" -i po/navcal.pot -o "po/$lang.po"
    fi
    mkdir -p "src/navcal/locale/$lang/LC_MESSAGES"
    msgfmt --check --statistics -o "src/navcal/locale/$lang/LC_MESSAGES/navcal.mo" "po/$lang.po"
done
