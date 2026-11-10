#!/usr/bin/env bash
# Runs the desktop tests in a throwaway X11 session: Xvfb, a window manager and, with
# COMPOSITOR=1, a compositing manager (translucent overlays need one to be see-through).
#
#   SCREEN    framebuffer, default 1920x1080x24
#   MONITORS  split the framebuffer into monitors, e.g. "1920x1080+0+0 1920x1080+1920+0"
#
# Display scaling comes from Qt's own variables (QT_SCALE_FACTOR, QT_SCREEN_SCALE_FACTORS).
set -euo pipefail
cd "$(dirname "$0")/../.."

export DISPLAY=:99
Xvfb :99 -screen 0 "${SCREEN:-1920x1080x24}" -nolisten tcp +extension Composite +extension RANDR +render &
xvfb=$!
trap 'kill $xvfb 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done

if [ -n "${MONITORS:-}" ]; then
  index=0
  for monitor in $MONITORS; do
    size="${monitor%%+*}"
    position="${monitor#*+}"
    width="${size%x*}"
    height="${size#*x}"
    # The first monitor replaces the one Xvfb reports for its single output ("screen").
    output=none
    [ "$index" = 0 ] && output=screen
    geometry="${width}/$((width * 254 / 960))x${height}/$((height * 254 / 960))+${position%+*}+${position#*+}"
    xrandr --setmonitor "MON$index" "$geometry" "$output"
    index=$((index + 1))
  done
  xrandr --listmonitors
fi

openbox &
sleep 1
if [ "${COMPOSITOR:-0}" = "1" ]; then
  picom --backend xrender --config /dev/null &
  sleep 1.5
fi

python -m pytest --desktop tests/integration -v "$@"
