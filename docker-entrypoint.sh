#!/bin/sh
# Starts a virtual X display for LibreCAD's bundled xcb plugin, then execs
# the MCP server so it becomes PID 1 with a clean, unshared stdio pipe.
#
# xvfb-run (Debian's wrapper) backgrounds Xvfb without redirecting its
# stdin away from the wrapped command's, so both processes end up racing
# to read the same long-lived stdin pipe — the MCP server never sees the
# first message it's waiting on. Explicitly pointing Xvfb's stdin at
# /dev/null avoids that race entirely.
set -e

DISPLAY_NUM=99
Xvfb ":${DISPLAY_NUM}" -screen 0 1280x1024x24 -nolisten tcp < /dev/null > /dev/null 2>&1 &

# Wait for the X socket to appear instead of a fixed sleep.
for _ in $(seq 1 50); do
    [ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
    sleep 0.1
done

export DISPLAY=":${DISPLAY_NUM}"
exec "$@"
