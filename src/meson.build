# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

# The Python package goes in the app's data folder; `navcal` in bindir starts it.
install_subdir('navcal',
  install_dir: pkgdatadir,
  exclude_directories: ['__pycache__', 'views/__pycache__', '.ruff_cache', 'locale', 'ui', 'icons'],
  exclude_files: ['data/navcal.gresource', 'navcal.gresource.xml', 'style.css'],
)

# UI files, CSS and icons, compiled from source.
gnome.compile_resources('navcal', 'navcal/navcal.gresource.xml',
  source_dir: 'navcal',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir / 'navcal' / 'data',
)

launcher = configuration_data()
launcher.set('PYTHON', python.full_path())
launcher.set('PKGDATADIR', pkgdatadir)
launcher.set('LOCALEDIR', localedir)
configure_file(
  input: 'navcal.in',
  output: 'navcal',
  configuration: launcher,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)
