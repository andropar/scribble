#!/bin/sh
# Installs Scribble for the current user (no root needed):
#
#   curl -fsSL https://raw.githubusercontent.com/andropar/scribble/master/python/packaging/install-linux.sh | sh
#
# or, from an unpacked release archive:  ./install.sh        Remove it again:  ./install.sh --uninstall
#
# Files go to ~/.local/opt/scribble, launchers to ~/.local/bin, plus a menu entry.
# Up to 0.4 the app was called voice-annotate; an installation of that name is replaced.
set -eu

APP=scribble
OLD=voice-annotate
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
TARGET="$HOME/.local/opt/$APP"
BIN="$HOME/.local/bin"
DESKTOP="$PREFIX/applications/$APP.desktop"
ICON="$PREFIX/icons/hicolor/256x256/apps/$APP.png"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
URL="https://github.com/andropar/scribble/releases/latest/download/scribble-linux-x86_64.tar.gz"

if [ "${1:-}" = "--uninstall" ]; then
  "$BIN/$APP-cli" quit >/dev/null 2>&1 || true
  rm -rf "$TARGET" "$BIN/$APP" "$BIN/$APP-cli" "$DESKTOP" "$ICON" "$AUTOSTART/$APP.desktop"
  echo "Scribble removed. Captures, settings and models are still in ~/.scribble."
  exit 0
fi

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "This build is for Linux on x86_64. On other systems install from source, see the README." >&2
  exit 1
fi

# Run from inside an unpacked archive, or download the latest release.
here=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || echo "")
cleanup=""
if [ -n "$here" ] && [ -x "$here/$APP" ] && [ -d "$here/_internal" ]; then
  source="$here"
else
  tmp=$(mktemp -d)
  cleanup="$tmp"
  echo "Downloading Scribble…"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar "$URL" -o "$tmp/$APP.tar.gz"
  else
    wget -q --show-progress "$URL" -O "$tmp/$APP.tar.gz"
  fi
  tar -xzf "$tmp/$APP.tar.gz" -C "$tmp"
  source="$tmp/$APP"
fi

"$BIN/$APP-cli" quit >/dev/null 2>&1 || true
# An installation under the old name: stop it, remove it, keep its start at login.
if [ -e "$HOME/.local/opt/$OLD" ] || [ -e "$BIN/$OLD" ]; then
  "$BIN/$OLD-cli" quit >/dev/null 2>&1 || true
  rm -rf "$HOME/.local/opt/$OLD" "$BIN/$OLD" "$BIN/$OLD-cli" \
    "$PREFIX/applications/$OLD.desktop" "$PREFIX/icons/hicolor/256x256/apps/$OLD.png"
fi
if [ -f "$AUTOSTART/$OLD.desktop" ]; then
  sed -e "s|^Exec=.*|Exec=$TARGET/$APP|" -e "s|^Name=.*|Name=Scribble|" "$AUTOSTART/$OLD.desktop" > "$AUTOSTART/$APP.desktop"
  rm -f "$AUTOSTART/$OLD.desktop"
fi
mkdir -p "$HOME/.local/opt" "$BIN" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
rm -rf "$TARGET"
cp -R "$source" "$TARGET"
ln -sf "$TARGET/$APP" "$BIN/$APP"
ln -sf "$TARGET/$APP-cli" "$BIN/$APP-cli"
cp "$TARGET/_internal/scribble/resources/icon.png" "$ICON"
cat > "$DESKTOP" <<ENTRY
[Desktop Entry]
Type=Application
Name=Scribble
Comment=Talk and draw on your screen, paste the result into any coding agent
Exec=$TARGET/$APP
Icon=$APP
Terminal=false
Categories=Utility;Development;
StartupNotify=false
ENTRY
update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1 || true
[ -n "$cleanup" ] && rm -rf "$cleanup"

echo "Installed. Start \"Scribble\" from your application menu, or run: $BIN/$APP"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH; the commands (scribble snapshot …) need the full path until it is." ;; esac
if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "Note: for the microphone, install PortAudio:  sudo apt install libportaudio2   (Fedora: sudo dnf install portaudio)"
fi
