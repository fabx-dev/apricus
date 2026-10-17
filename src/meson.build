pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
gnome = import('gnome')

gnome.compile_resources('apricus',
  'apricus.gresource.xml',
  source_dir: 'apricus',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'apricus.in',
  output: 'apricus',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir')
)

# Install the `apricus` package (dev and installed trees share this layout).
install_subdir('apricus',
  install_dir: pkgdatadir,
  exclude_directories: ['__pycache__', 'gui/__pycache__', 'player/__pycache__'],
)
