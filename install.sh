#!/bin/zsh
# Installs (or updates) Dumac Download Manager for the current user. Run it again any time to update.
#
# Needs: macOS 13+, Apple's Command Line Tools (for swiftc), Homebrew.
# Installs: aria2, deno, ffmpeg, python (Homebrew, only if missing); yt-dlp in a private venv;
#           ~/Applications/Dumac Download Manager.app; the browser helper for Brave / Chrome / Edge / Vivaldi / Chromium.
# Nothing is installed system-wide, and no background job or login item is added.
set -e
REPO="${0:A:h}"
NAME="Dumac Download Manager"
SUPPORT="$HOME/Library/Application Support/$NAME"
APP="$HOME/Applications/$NAME.app"
HOST_ID="io.github.kaydudrone.dumac"
EXT_ID="iljmdiebkgfjmnmdfklnehomopkjncho"   # fixed by the "key" in extension/manifest.json

step() { print -P "\n%F{blue}==>%f %B$*%b"; }
fail() { print -P "%F{red}Error:%f $*"; exit 1; }

[[ "$(uname)" == Darwin ]] || fail "Dumac Download Manager runs on macOS only."
xcrun --find swiftc >/dev/null 2>&1 || fail "Apple's Command Line Tools are missing. Run:  xcode-select --install  then run this installer again."
BREW="$(command -v brew || true)"
[[ -z "$BREW" && -x /opt/homebrew/bin/brew ]] && BREW=/opt/homebrew/bin/brew
[[ -z "$BREW" && -x /usr/local/bin/brew ]] && BREW=/usr/local/bin/brew
[[ -n "$BREW" ]] || fail "Homebrew is missing. Install it from https://brew.sh then run this installer again."
PREFIX="$("$BREW" --prefix)"

step "Checking tools (aria2, deno, ffmpeg, python)"
need() {  # need <command> <brew formula>
  if [[ -x "$PREFIX/bin/$1" ]] || command -v "$1" >/dev/null 2>&1; then echo "  ✓ $1"; else "$BREW" install "$2"; fi
}
need aria2c aria2
need deno deno
need ffmpeg ffmpeg
need python3 python
PY="$PREFIX/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { "$BREW" install python; PY="$PREFIX/bin/python3"; }

step "Installing yt-dlp (private copy, kept up to date by re-running this installer)"
mkdir -p "$SUPPORT"
[[ -x "$SUPPORT/venv/bin/python" ]] || "$PY" -m venv "$SUPPORT/venv"
"$SUPPORT/venv/bin/python" -m pip install --quiet --upgrade pip "yt-dlp[default]"
echo "  ✓ yt-dlp $("$SUPPORT/venv/bin/yt-dlp" --version)"

step "Installing the helper"
cp "$REPO/host/dumac_host.py" "$SUPPORT/dumac_host.py"
cat > "$SUPPORT/dumac-host" <<EOF
#!/bin/sh
exec "$SUPPORT/venv/bin/python" "$SUPPORT/dumac_host.py" "\$@"
EOF
chmod +x "$SUPPORT/dumac-host"

step "Connecting browsers"
for dir in "BraveSoftware/Brave-Browser" "Google/Chrome" "Microsoft Edge" "Vivaldi" "Chromium"; do
  base="$HOME/Library/Application Support/$dir"
  [[ -d "$base" ]] || continue
  mkdir -p "$base/NativeMessagingHosts"
  cat > "$base/NativeMessagingHosts/$HOST_ID.json" <<EOF
{
  "name": "$HOST_ID",
  "description": "$NAME helper",
  "path": "$SUPPORT/dumac-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF
  echo "  ✓ ${dir:t}"
done

step "Building the app"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
swiftc -swift-version 5 -O "$REPO/macapp/main.swift" -o "$BUILD/DownloadManager" -framework Cocoa -framework WebKit
# App icon: macapp/icon/ (1024 master + hand-tuned 16/32 px versions that stay sharp in Finder lists).
ICON="$REPO/macapp/icon"
mkdir "$BUILD/AppIcon.iconset"
cp "$ICON/dumac-icon-D-small-16.png" "$BUILD/AppIcon.iconset/icon_16x16.png"
cp "$ICON/dumac-icon-D-small-32.png" "$BUILD/AppIcon.iconset/icon_16x16@2x.png"
cp "$ICON/dumac-icon-D-small-32.png" "$BUILD/AppIcon.iconset/icon_32x32.png"
for px in 64:icon_32x32@2x 128:icon_128x128 256:icon_128x128@2x 256:icon_256x256 512:icon_256x256@2x 512:icon_512x512 1024:icon_512x512@2x; do
  sips -z ${px%%:*} ${px%%:*} "$ICON/dumac-icon-A-default.png" --out "$BUILD/AppIcon.iconset/${px#*:}.png" >/dev/null
done
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$BUILD/AppIcon.icns"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/web"
cp "$REPO/macapp/Info.plist" "$APP/Contents/"
cp "$BUILD/DownloadManager" "$APP/Contents/MacOS/"
cp "$BUILD/AppIcon.icns" "$REPO/macapp/shim.js" "$APP/Contents/Resources/"
cp "$REPO/extension/manager.html" "$REPO/extension/manager.css" "$REPO/extension/manager.js" "$APP/Contents/Resources/web/"
codesign --force --sign - "$APP" 2>/dev/null   # ad-hoc signature: built on this Mac, so Gatekeeper doesn't block it
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
echo "  ✓ $APP"

step "Done"
cat <<EOF

Next steps:
  1. Open "$NAME" from Launchpad. If macOS asks for access to your Downloads folder, click Allow.
  2. Browser button + download takeover (Brave, Chrome, Edge, Vivaldi):
       open the browser's extensions page (e.g. brave://extensions or chrome://extensions),
       turn on Developer mode, click "Load unpacked" and pick:
       $REPO/extension
  3. Safari: open $REPO/macapp/safari-button.html in Safari and drag the button to the Favorites bar.

Keep this folder: the browser extension is loaded from it.
EOF
