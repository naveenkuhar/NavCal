# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

project('navcal',
  version: '0.6.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0',
)

app_id = 'io.github.navcal.Navcal'
gnome = import('gnome')
i18n = import('i18n')
python = import('python').find_installation('python3')

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
localedir = prefix / get_option('localedir')
pkgdatadir = datadir / meson.project_name()

subdir('src')
subdir('data')
subdir('po')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
