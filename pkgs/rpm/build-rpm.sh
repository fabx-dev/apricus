#!/usr/bin/env bash
# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build a native Fedora RPM package (dist/apricus-<version>-1.<arch>.rpm)
# from the current Meson source tree. The package is arch-specific because
# it bundles the pinned Deno runtime (see pkgs/deno.env, same release as
# the Flatpak and Snap builds) under /usr/lib/apricus/deno.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(sed -n "s/.*version:[[:space:]]*'\([^']*\)'.*/\1/p" meson.build | head -n1)"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: could not extract version from meson.build" >&2
    exit 1
fi

# Fedora does not ship a deno package: bundle the pinned upstream binary
# (same release as Flatpak/Snap) so yt-dlp can solve YouTube JS challenges.
case "$(uname -m)" in
    x86_64) RPM_ARCH="x86_64"; DENO_ARCH="x86_64" ;;
    aarch64) RPM_ARCH="aarch64"; DENO_ARCH="aarch64" ;;
    *) echo "ERROR: bundled deno unsupported on: $(uname -m)" >&2; exit 1 ;;
esac

RPM_VERSION="${VERSION//-/_}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/apricus-rpm.XXXXXX")"
BUILD_DIR="$WORK_DIR/meson-build"
STAGE_DIR="$WORK_DIR/stage"
RPMTOP="$WORK_DIR/rpmbuild"

trap 'rm -rf "$WORK_DIR"' EXIT

meson setup --prefix=/usr "$BUILD_DIR"
meson compile -C "$BUILD_DIR"
DESTDIR="$STAGE_DIR" meson install -C "$BUILD_DIR" --no-rebuild

rm -f "$STAGE_DIR/usr/share/glib-2.0/schemas/gschemas.compiled"
rm -f "$STAGE_DIR/usr/share/icons/hicolor/icon-theme.cache"
find "$STAGE_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} +

# Bundle the pinned Deno runtime (same release as Flatpak/Snap) so yt-dlp
# can solve YouTube JS challenges without any system deno. Installed to a
# private path to never shadow a user/system deno on PATH.
# shellcheck disable=SC1091
source "$ROOT_DIR/pkgs/deno.env"
DENO_URL_VAR="DENO_URL_${DENO_ARCH}"
DENO_SHA_VAR="DENO_SHA256_${DENO_ARCH}"
DENO_ZIP="$WORK_DIR/deno.zip"
python3 - "$DENO_ZIP" "${!DENO_URL_VAR}" <<'PY'
import sys
import urllib.request

dest, url = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "apricus-build"})
with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
    while True:
        chunk = resp.read(1048576)
        if not chunk:
            break
        f.write(chunk)
print(f"Downloaded {url}", flush=True)
PY
echo "${!DENO_SHA_VAR}  $DENO_ZIP" | sha256sum -c -
python3 - "$DENO_ZIP" "$WORK_DIR" <<'PY'
import sys
import zipfile

archive, destdir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive) as zf:
    zf.extract("deno", destdir)
print("Extracted deno", flush=True)
PY
install -Dm755 "$WORK_DIR/deno" "$STAGE_DIR/usr/lib/apricus/deno"
"$STAGE_DIR/usr/lib/apricus/deno" --version

mkdir -p "$RPMTOP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

cat > "$RPMTOP/SPECS/apricus.spec" <<EOF
%global _build_id_links none
%define __brp_mangle_shebangs %{nil}
%define __brp_python_bytecompile %{nil}

Name:           apricus
Version:        ${RPM_VERSION}
Release:        1
Summary:        Modern live wallpaper for the GNOME desktop on Linux
License:        GPL-3.0-or-later
URL:            https://github.com/fabx-dev/apricus
BuildArch:      ${RPM_ARCH}

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       python3-dbus
Requires:       python3-pydbus
Requires:       python3-requests
Requires:       python3-setproctitle
Requires:       python3-vlc
Requires:       gtk4
Requires:       libadwaita
Requires:       gtk3
Requires:       gdk-pixbuf2
Requires:       gnome-desktop4
Requires:       webkit2gtk4.1
Requires:       libwnck3
Requires:       (libayatana-appindicator-gtk3 or libappindicator-gtk3)
Requires:       vlc
Requires:       (ffmpeg-free or ffmpeg)
Requires:       dconf
Requires:       xdg-user-dirs
Requires:       yt-dlp

%description
Apricus transforms your GNOME desktop background on Linux into a fluid,
living canvas supporting local videos, muted looping playlists, live
streams (YouTube, Twitch, HLS/direct) and interactive web pages.

%install
cp -a "${STAGE_DIR}/usr" "%{buildroot}/usr"

%files
%{_bindir}/apricus
%{_datadir}/apricus
%{_prefix}/lib/apricus/deno
%{_datadir}/applications/io.github.fabx_dev.Apricus.desktop
%{_datadir}/metainfo/io.github.fabx_dev.Apricus.metainfo.xml
%{_datadir}/glib-2.0/schemas/io.github.fabx_dev.Apricus.gschema.xml
%{_datadir}/icons/hicolor/*/apps/io.github.fabx_dev.Apricus.png
%{_datadir}/licenses/io.github.fabx_dev.Apricus
%{_datadir}/locale/*/LC_MESSAGES/apricus.mo
EOF

rpmbuild --define "_topdir $RPMTOP" -bb "$RPMTOP/SPECS/apricus.spec"

mkdir -p "$ROOT_DIR/dist"
OUT_RPM="$ROOT_DIR/dist/apricus-${RPM_VERSION}-1.${RPM_ARCH}.rpm"
cp "$RPMTOP/RPMS/${RPM_ARCH}"/apricus-*.${RPM_ARCH}.rpm "$OUT_RPM"
echo "Built: $OUT_RPM"
