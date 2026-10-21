#!/bin/bash
# Host-side installer for the Apricus GNOME Shell integration.
#
# Snap and Flatpak sandboxes cannot install host extensions themselves, so
# run this script yourself on the host (no sudo needed):
#
#   ./install-host.sh [--enable]
#
# It copies apricus-integration@fabx-dev into
# ~/.local/share/gnome-shell/extensions/ (the per-user extension dir that
# native packages and `make install` also cover system-wide). With --enable
# it additionally enables the extension. On Wayland, GNOME Shell only
# discovers newly installed extensions after a logout/login.
set -euo pipefail

UUID="apricus-integration@fabx-dev"
SRC_DIR="$(cd "$(dirname "$0")/$UUID" && pwd)"
DEST_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
ENABLE=0

for arg in "$@"; do
    case "$arg" in
        --enable) ENABLE=1 ;;
        -h|--help)
            echo "Usage: $0 [--enable]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

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
