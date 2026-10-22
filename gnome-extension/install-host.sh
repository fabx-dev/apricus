#!/bin/bash
# Host-side installer for the Apricus GNOME Shell integration.
#
# Snap and Flatpak sandboxes cannot install host extensions themselves, so
# run this script yourself on the host (no sudo needed). One installer for
# both: it only ever touches your own ~/.local directory.
#
# From a source checkout:
#   ./install-host.sh [--enable]
#
# From the distributable ZIP (preferred for Snap/Flatpak users, no repo needed):
#   ./install-host.sh --zip apricus-integration@fabx-dev.zip [--enable]
#
# It installs apricus-integration@fabx-dev into
# ~/.local/share/gnome-shell/extensions/ (the per-user extension dir that
# native packages and `make install` also cover system-wide). With --enable
# it additionally enables the extension. On Wayland, GNOME Shell only
# discovers newly installed extensions after a logout/login.
set -euo pipefail

UUID="apricus-integration@fabx-dev"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$SCRIPT_DIR/$UUID"
DEST_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
ENABLE=0
ZIP=""
EXPECT_ZIP=0

for arg in "$@"; do
    if [ "$EXPECT_ZIP" -eq 1 ]; then
        ZIP="$arg"
        EXPECT_ZIP=0
        continue
    fi
    case "$arg" in
        --enable) ENABLE=1 ;;
        --zip) EXPECT_ZIP=1 ;;
        --zip=*) ZIP="${arg#--zip=}" ;;
        -h|--help)
            echo "Usage: $0 [--zip PATH] [--enable]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done
if [ "$EXPECT_ZIP" -eq 1 ]; then
    echo "Missing PATH after --zip" >&2
    exit 1
fi

if [ -n "$ZIP" ]; then
    command -v unzip >/dev/null 2>&1 || { echo "unzip not found (install 'unzip')" >&2; exit 1; }
    [ -f "$ZIP" ] || { echo "ZIP not found: $ZIP" >&2; exit 1; }
    TMPDIR_WORK="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR_WORK"' EXIT
    unzip -q -o "$ZIP" -d "$TMPDIR_WORK"
    if [ -d "$TMPDIR_WORK/$UUID" ]; then
        SRC_DIR="$TMPDIR_WORK/$UUID"
    else
        echo "ZIP does not contain $UUID/ (see build-zip.sh)" >&2
        exit 1
    fi
fi

for req in metadata.json extension.js; do
    [ -f "$SRC_DIR/$req" ] || { echo "Missing $req in $SRC_DIR" >&2; exit 1; }
done

# Warn (don't fail) when the running Shell is outside the declared range.
if command -v gnome-shell >/dev/null 2>&1; then
    SHELL_VER="$(gnome-shell --version 2>/dev/null | awk '{print $NF}')"
    SHELL_MAJOR="${SHELL_VER%%.*}"
    if [ -n "$SHELL_MAJOR" ] && ! grep -q "\"$SHELL_MAJOR\"" "$SRC_DIR/metadata.json"; then
        echo "WARNING: GNOME Shell $SHELL_VER is not in the extension's supported list." >&2
        echo "The integration will stay disabled until a compatible Apricus ships." >&2
    fi
fi

mkdir -p "$DEST_DIR"
cp "$SRC_DIR/metadata.json" "$SRC_DIR/extension.js" "$DEST_DIR/"
echo "Installed $UUID to $DEST_DIR"

if [ "$ENABLE" -eq 1 ]; then
    if command -v gnome-extensions >/dev/null 2>&1; then
        if gnome-extensions enable "$UUID"; then
            echo "Enabled. Log out and back in so GNOME Shell loads it."
        else
            echo "Installed, but enabling failed (Shell may not know it yet)."
            echo "Log out and back in, then run: gnome-extensions enable $UUID"
        fi
    else
        echo "gnome-extensions not found; enable it from the Extensions app."
    fi
else
    echo "To enable: gnome-extensions enable $UUID (then log out and back in)."
fi
