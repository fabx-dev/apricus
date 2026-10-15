application_id = 'io.github.fabx_dev.Apricus'

scalable_dir = join_paths('hicolor', 'scalable', 'apps')
install_data(
  join_paths(scalable_dir, ('@0@.svg').format(application_id)),
  install_dir: join_paths(get_option('datadir'), 'icons', scalable_dir)
)

icon_sizes = ['16x16', '24x24', '32x32', '48x48', '64x64', '128x128', '256x256', '512x512']
foreach size : icon_sizes
  size_dir = join_paths('hicolor', size, 'apps')
  install_data(
    join_paths(size_dir, ('@0@.png').format(application_id)),
    install_dir: join_paths(get_option('datadir'), 'icons', size_dir)
  )
endforeach