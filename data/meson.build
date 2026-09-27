# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

desktop_file = i18n.merge_file(
  input: app_id + '.desktop.in',
  output: app_id + '.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'applications',
)

metainfo_file = i18n.merge_file(
  input: app_id + '.metainfo.xml.in',
  output: app_id + '.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'metainfo',
)

# Lets GNOME start Navcal for Activities searches and notification buttons.
service = configuration_data()
service.set('APP_ID', app_id)
service.set('BINDIR', bindir)
configure_file(
  input: app_id + '.service.in',
  output: app_id + '.service',
  configuration: service,
  install: true,
  install_dir: datadir / 'dbus-1' / 'services',
)

install_data('../src/navcal/data/search-provider.ini',
  rename: app_id + '.search-provider.ini',
  install_dir: datadir / 'gnome-shell' / 'search-providers',
)
install_data('../src/navcal/data/icon.svg',
  rename: app_id + '.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data('../src/navcal/data/icon-symbolic.svg',
  rename: app_id + '-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

desktop_file_validate = find_program('desktop-file-validate', required: false)
if desktop_file_validate.found()
  test('validate-desktop', desktop_file_validate, args: [desktop_file])
endif
appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  # Errors fail; warnings (like the homepage link, until there is one) are only shown.
  test('validate-metainfo', find_program('sh'), args: ['-c',
    'out=$("$1" validate --no-net --explain "$2" 2>&1); echo "$out"; ! echo "$out" | grep -q "^E:"',
    'validate', appstreamcli, metainfo_file])
endif
