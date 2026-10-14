#!/bin/zsh
# Removes Dumac Download Manager. Your downloaded files are never touched.
# Homebrew tools (aria2, deno, ffmpeg, python) stay installed; remove them with `brew uninstall` if you like.
NAME="Dumac Download Manager"
HOST_ID="io.github.kaydudrone.dumac"
osascript -e "quit app \"$NAME\"" 2>/dev/null
rm -rf "$HOME/Applications/$NAME.app"
for dir in "BraveSoftware/Brave-Browser" "Google/Chrome" "Microsoft Edge" "Vivaldi" "Chromium"; do
  rm -f "$HOME/Library/Application Support/$dir/NativeMessagingHosts/$HOST_ID.json"
done
read "keep?Also delete the download list and settings? [y/N] "
[[ "$keep" == [yY]* ]] && rm -rf "$HOME/Library/Application Support/$NAME"
echo "Removed. Also remove the extension from your browser's extensions page, and the Safari bookmark if you added it."
