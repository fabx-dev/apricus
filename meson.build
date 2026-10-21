project('apricus',
          version: '2.0.7',
          license: ['GPL-3.0'],
    meson_version: '>= 0.59.0',
  default_options: [ 'warning_level=2',
                     'werror=false',
                   ],
)

i18n = import('i18n')

gnome = import('gnome')

subdir('data')
subdir('src')
subdir('po')

# GNOME Shell wallpaper integration: installed where the Shell looks for
# system extensions, so .deb/.rpm carry it automatically (DESTDIR staging).
# The user still enables it once with `gnome-extensions enable`.
install_subdir('gnome-extension/apricus-integration@fabx-dev',
  install_dir: join_paths(get_option('datadir'), 'gnome-shell/extensions'),
)

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
