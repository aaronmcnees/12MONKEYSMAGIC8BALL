#!/usr/bin/env bash
set -u

cd "$(dirname "$0")"
HERE="$(pwd -P)"
FOUND=0

for pid in $(pgrep -f '(^|/)python3? .*MAGIC8BALL\.py|python3? MAGIC8BALL\.py' 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    [ -d "/proc/$pid" ] || continue

    CWD="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    CMD="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"

    if [ "$CWD" = "$HERE" ] && [[ "$CMD" == *"MAGIC8BALL.py"* ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        FOUND=1
    fi
done

# Give the backend a moment to exit cleanly.
for _ in 1 2 3 4 5; do
    LEFT=0
    for pid in $(pgrep -f '(^|/)python3? .*MAGIC8BALL\.py|python3? MAGIC8BALL\.py' 2>/dev/null || true); do
        [ -d "/proc/$pid" ] || continue
        CWD="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
        CMD="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        if [ "$CWD" = "$HERE" ] && [[ "$CMD" == *"MAGIC8BALL.py"* ]]; then
            LEFT=1
            break
        fi
    done
    [ "$LEFT" -eq 0 ] && exit 0
    sleep 0.2
done

# If a backend ignored TERM, stop only the MAGIC 8-BALL process from this folder.
for pid in $(pgrep -f '(^|/)python3? .*MAGIC8BALL\.py|python3? MAGIC8BALL\.py' 2>/dev/null || true); do
    [ -d "/proc/$pid" ] || continue
    CWD="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    CMD="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [ "$CWD" = "$HERE" ] && [[ "$CMD" == *"MAGIC8BALL.py"* ]]; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
done

exit 0
