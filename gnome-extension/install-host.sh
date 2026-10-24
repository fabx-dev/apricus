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
CHECK=0
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
        --check) CHECK=1 ;;
        --enable) ENABLE=1 ;;
        --zip) EXPECT_ZIP=1 ;;
        --zip=*) ZIP="${arg#--zip=}" ;;
        -h|--help)
            echo "Usage: $0 [--zip PATH] [--enable | --check]"
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

if [ "$CHECK" -eq 1 ] && { [ "$ENABLE" -eq 1 ] || [ -z "$ZIP" ]; }; then
    echo "--check requires --zip PATH and cannot be combined with --enable" >&2
    exit 1
fi

if [ "$CHECK" -eq 0 ]; then
if [ -n "$ZIP" ]; then
    command -v unzip >/dev/null 2>&1 || { echo "unzip not found (install 'unzip')" >&2; exit 1; }
    [ -f "$ZIP" ] || { echo "ZIP not found: $ZIP" >&2; exit 1; }
    TMPDIR_WORK="$(mktemp -d)"
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
# Replace each file atomically (temp copy + rename) so the Shell never
# observes a half-written metadata.json/extension.js, e.g. when this
# script re-runs as an update while the extension is loaded. Cleanup of
# the ZIP workdir and leftover temp files on interruption.
trap 'if [ -n "${TMPDIR_WORK:-}" ]; then rm -rf "$TMPDIR_WORK"; fi; rm -f "$DEST_DIR"/*.tmp.$$' EXIT
install_file() {
    tmp="$2.tmp.$$"
    cp "$1" "$tmp"
    mv -f "$tmp" "$2"
}
install_file "$SRC_DIR/metadata.json" "$DEST_DIR/metadata.json"
if [ -f "$SRC_DIR/statusBridge.js" ]; then
    install_file "$SRC_DIR/statusBridge.js" "$DEST_DIR/statusBridge.js"
fi
install_file "$SRC_DIR/extension.js" "$DEST_DIR/extension.js"
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

fi

# Host-verified status receipt for sandboxed Apricus (Snap/Flatpak cannot
# see ~/.local and cannot query the Shell over D-Bus, so Settings can
# never confirm Active by itself). Written next to the ZIP only in
# --zip mode; the GUI reads it on "Check again" and trusts it only
# while fresh (see RECEIPT_MAX_AGE_SEC). Re-run this script to refresh
# after logout/login using --check: it never installs or enables anything.
if [ -n "$ZIP" ]; then
    _receipt_dir="$(dirname "$ZIP")"
    _shell_ver=""
    if command -v gnome-shell >/dev/null 2>&1; then
        _shell_ver="$(gnome-shell --version 2>/dev/null | awk '{print $NF}')"
    fi
    _installed=false
    [ ! -f "$DEST_DIR/metadata.json" ] || _installed=true
    _enabled=0
    _loaded=0
    _verified=false
    _state=""
    if _info="$(gdbus call --session --dest org.gnome.Shell.Extensions \
        --object-path /org/gnome/Shell/Extensions \
        --method org.gnome.Shell.Extensions.GetExtensionInfo "$UUID" 2>/dev/null)"; then
        _verified=true
        _state="$(printf '%s' "$_info" | sed -n "s/.*'state': <\([0-9][0-9]*\)\(\.0\)\{0,1\}>.*/\1/p")"
        if [ "$_state" = 1 ]; then _loaded=1; _enabled=1; _installed=true; fi
        if [ -n "$_state" ]; then _installed=true; fi
        if command -v gnome-extensions >/dev/null 2>&1; then
            if gnome-extensions list --enabled 2>/dev/null | grep -qx "$UUID"; then
                _enabled=1
            fi
        fi
    fi
    _bool() { if [ "$1" -eq 1 ]; then printf 'true'; else printf 'false'; fi; }
    _now="$(date +%s)"
    {
        printf '{\n'
        printf '  "uuid": "%s",\n' "$UUID"
        printf '  "installed": %s,\n' "$_installed"
        printf '  "enabled": %s,\n' "$(_bool "$_enabled")"
        printf '  "loaded": %s,\n' "$(_bool "$_loaded")"
        printf '  "verified": %s,\n' "$_verified"
        printf '  "state": "%s",\n' "$_state"
        if [ -n "$_shell_ver" ]; then
            printf '  "shell": "%s",\n' "$_shell_ver"
        else
            printf '  "shell": null,\n'
        fi
        printf '  "checked_at": %s\n}\n' "$_now"
    } > "$_receipt_dir/status.json.tmp.$$"
    mv -f "$_receipt_dir/status.json.tmp.$$" "$_receipt_dir/status.json"
    echo "Status written to $_receipt_dir/status.json"
fi
