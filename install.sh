#!/bin/sh
# Link the plugin into Glyphs 3 so edits here are picked up after a Glyphs restart.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/Library/Application Support/Glyphs 3/Plugins"
mkdir -p "$dest"
ln -sfn "$here/KnotStroke.glyphsFilter" "$dest/KnotStroke.glyphsFilter"
echo "Linked into: $dest"
echo "Restart Glyphs. Then install the Python and skia-pathops modules (Settings > Addons > Modules) if you haven't."
