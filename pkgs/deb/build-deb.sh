#!/usr/bin/env bash
# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build a native Debian/Ubuntu package (dist/apricus_<version>_all.deb)
# from the current Meson source tree.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(sed -n "s/.*version:[[:space:]]*'\([^']*\)'.*/\1/p" meson.build | head -n1)"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: could not extract version from meson.build" >&2
    exit 1
fi

DEB_VERSION="${VERSION//-/\~}"
PKG_NAME="apricus"
ARCH="all"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/apricus-deb.XXXXXX")"
BUILD_DIR="$WORK_DIR/meson-build"
STAGE_DIR="$WORK_DIR/${PKG_NAME}_${DEB_VERSION}_${ARCH}"

trap 'rm -rf "$WORK_DIR"' EXIT

meson setup --prefix=/usr "$BUILD_DIR"
meson compile -C "$BUILD_DIR"
DESTDIR="$STAGE_DIR" meson install -C "$BUILD_DIR" --no-rebuild

# Remove compiled schema/icon caches generated in DESTDIR (postinst handles them)
rm -f "$STAGE_DIR/usr/share/glib-2.0/schemas/gschemas.compiled"
rm -f "$STAGE_DIR/usr/share/icons/hicolor/icon-theme.cache"
find "$STAGE_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} +

mkdir -p "$STAGE_DIR/DEBIAN"

INSTALLED_KB="$(du -sk "$STAGE_DIR/usr" | awk '{print $1}')"

cat > "$STAGE_DIR/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_KB}
Maintainer: Fabrizio (fabx-dev) <fabxdev@proton.me>
Homepage: https://github.com/fabx-dev/apricus
Depends: python3 (>= 3.10),
 python3-gi,
 python3-gi-cairo,
 python3-dbus,
 python3-pydbus,
 python3-requests,
 python3-setproctitle,
 python3-vlc,
 gir1.2-gtk-4.0,
 gir1.2-adw-1,
 gir1.2-gtk-3.0,
 gir1.2-gdkpixbuf-2.0,
 gir1.2-gnomedesktop-4.0 | gir1.2-gnomedesktop-3.0,
 gir1.2-webkit2-4.1,
 gir1.2-wnck-3.0,
 gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1,
 vlc,
 vlc-plugin-base,
 vlc-plugin-video-output,
 ffmpeg,
 dconf-cli,
 xdg-user-dirs,
 yt-dlp
Recommends: totem-video-thumbnailer
Description: Modern live wallpaper for the GNOME desktop on Linux
 Apricus transforms your GNOME desktop background on Linux into a fluid,
 living canvas supporting local videos, muted looping playlists, live
 streams (YouTube, Twitch, HLS/direct) and interactive web pages.
 .
 Features a native GTK4 and Libadwaita control center, gapless in-place
 video switching, visual playlists with drag-and-drop and undo, a live
 Now Playing dashboard, and fast login startup.
EOF

cat > "$STAGE_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    if command -v glib-compile-schemas >/dev/null 2>&1; then
        glib-compile-schemas /usr/share/glib-2.0/schemas || true
    fi
    if command -v gtk4-update-icon-cache >/dev/null 2>&1; then
        gtk4-update-icon-cache -f -t /usr/share/icons/hicolor || true
    elif command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
fi
exit 0
EOF
chmod 0755 "$STAGE_DIR/DEBIAN/postinst"

mkdir -p "$ROOT_DIR/dist"
OUT_DEB="$ROOT_DIR/dist/${PKG_NAME}_${DEB_VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$STAGE_DIR" "$OUT_DEB"
echo "Built: $OUT_DEB"
