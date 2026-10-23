#!/bin/bash
# Build a distributable ZIP of the Apricus GNOME Shell extension.
#
# Usage: ./build-zip.sh [OUTPUT]
# Default output: dist/apricus-integration@fabx-dev.zip
#
# The archive contains the top-level directory apricus-integration@fabx-dev/
# with the files GNOME Shell needs (metadata.json, extension.js, statusBridge.js),
# so users install it by unzipping straight into their extensions dir:
#
#   unzip -o apricus-integration@fabx-dev.zip -d ~/.local/share/gnome-shell/extensions/
#
# Snap/Flatpak users should prefer gnome-extension/install-host.sh, which
# wraps this flow (download/copy + enable + version check).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/apricus-integration@fabx-dev" && pwd)"
OUT="${1:-$(cd "$(dirname "$0")/.." && pwd)/dist/apricus-integration@fabx-dev.zip}"

command -v zip >/dev/null 2>&1 || { echo "zip not found (install 'zip')" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }

# Sanity: the metadata must parse and declare supported Shell versions.
python3 -c "import json,sys; m=json.load(open('$SRC_DIR/metadata.json')); assert m.get('uuid')=='apricus-integration@fabx-dev', 'bad uuid'; assert m.get('shell-version'), 'no shell-version'; print('metadata ok:', m['shell-version'])"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
(
    cd "$(dirname "$SRC_DIR")"
    zip -q -r -X "$OUT" "apricus-integration@fabx-dev" -x "apricus-integration@fabx-dev/*.sh"
)
echo "Built: $OUT"
unzip -l "$OUT"
