#!/usr/bin/env bash
# Runs the desktop tests in a throwaway X11 session: Xvfb, a window manager and, with
# COMPOSITOR=1, a compositing manager (translucent overlays need one to be see-through).
#
#   SCREEN    framebuffer, default 1920x1080x24
#
# Display scaling comes from Qt's own variable: QT_SCALE_FACTOR=1.5 with SCREEN=2880x1620x24
# is a 1920x1080 desktop at 150%. (Several monitors cannot be had this way: Xvfb accepts
# `xrandr --setmonitor` and ignores it. tests/test_multi_display.py emulates them instead.)
set -euo pipefail
cd "$(dirname "$0")/../.."

export DISPLAY=:99
Xvfb :99 -screen 0 "${SCREEN:-1920x1080x24}" -nolisten tcp +extension Composite +extension RANDR +render &
xvfb=$!
trap 'kill $xvfb 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done

openbox &
sleep 1
if [ "${COMPOSITOR:-0}" = "1" ]; then
  picom --backend xrender --config /dev/null &
  sleep 1.5
fi

python -m pytest --desktop tests/integration -v "$@"
