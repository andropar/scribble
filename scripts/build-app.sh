#!/usr/bin/env bash
# Builds, bundles and signs voice-annotate.app into ./build.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${CONFIG:-release}"
APP="build/voice-annotate.app"

swift build -c "$CONFIG" --product VoiceAnnotate
BIN="$(swift build -c "$CONFIG" --show-bin-path)/VoiceAnnotate"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/VoiceAnnotate"
cp Resources/Info.plist "$APP/Contents/Info.plist"

ICONSET="build/AppIcon.iconset"
if [ ! -f build/AppIcon.icns ] || [ scripts/make-icon.swift -nt build/AppIcon.icns ]; then
  rm -rf "$ICONSET"
  swift scripts/make-icon.swift "$ICONSET"
  iconutil -c icns "$ICONSET" -o build/AppIcon.icns
fi
cp build/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

# Stable signing identity keeps macOS privacy grants (Screen Recording, Mic) across rebuilds.
IDENTITY="${SIGN_IDENTITY:-$(security find-identity -v -p codesigning | awk -F'"' '/Apple Development/ {print $2; exit}')}"
if [ -z "$IDENTITY" ]; then
  echo "warning: no Apple Development identity found, signing ad-hoc (permissions reset on every build)" >&2
  IDENTITY="-"
fi
codesign --force --options runtime --timestamp=none \
  --entitlements Resources/VoiceAnnotate.entitlements \
  --sign "$IDENTITY" "$APP"
codesign --verify --strict "$APP"
echo "Built $APP (signed with: $IDENTITY)"
