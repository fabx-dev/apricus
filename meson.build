project('apricus',
          version: '2.1.0',
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

# Snapcraft exports CRAFT_PART_INSTALL during Meson configure. Keep the native
# TCP fallback out of DEB/RPM/Flatpak builds and leave snapcraft.yaml unchanged.
python = import('python').find_installation('python3')
snap_build = run_command(python, '-c',
  'import os; print(int(bool(os.environ.get("CRAFT_PART_INSTALL") or os.environ.get("SNAPCRAFT_PART_INSTALL"))))',
  check: true).stdout().strip() == '1'
if snap_build
  add_languages('c', required: true)
  cc = meson.get_compiler('c')
  shared_library('apricus-vlc-fastopen', 'src/compat/vlc_fastopen.c',
    dependencies: cc.find_library('dl'),
    install: true,
    install_dir: join_paths(get_option('prefix'), 'lib', 'apricus'))
endif
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
