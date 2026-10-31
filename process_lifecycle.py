#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE / "MAGIC8BALL.py"
PIDFILE = BASE / ".magic8ball.pid"


def _proc_info(pid):
    proc = Path("/proc") / str(pid)
    try:
        cwd = Path(os.readlink(proc / "cwd")).resolve()
        cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        return cwd, cmd
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None


def _owned(pid):
    info = _proc_info(pid)
    if not info:
        return False
    cwd, cmd = info
    return cwd == BASE and "MAGIC8BALL.py" in cmd


def _write_pid(pid):
    PIDFILE.write_text(json.dumps({"pid": pid, "base": str(BASE)}) + "\n", encoding="utf-8")


def _read_pid():
    try:
        data = json.loads(PIDFILE.read_text(encoding="utf-8"))
        return int(data["pid"])
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError, OSError):
        return None


def _remove_pidfile():
    try:
        PIDFILE.unlink()
    except FileNotFoundError:
        pass


def _find_owned():
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid != os.getpid() and _owned(pid):
            found.append(pid)
    return found


def stop():
    pid = _read_pid()
    targets = []
    if pid and _owned(pid):
        targets.append(pid)
    else:
        targets = _find_owned()

    if not targets:
        _remove_pidfile()
        print("12 MONKEYS MAGIC 8-BALL is not running.")
        return 0

    for target in targets:
        try:
            os.kill(target, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not any(_owned(target) for target in targets):
            _remove_pidfile()
            print("12 MONKEYS MAGIC 8-BALL stopped.")
            return 0
        time.sleep(0.1)

    for target in targets:
        if _owned(target):
            try:
                os.kill(target, signal.SIGKILL)
            except ProcessLookupError:
                pass

    _remove_pidfile()
    print("12 MONKEYS MAGIC 8-BALL stopped.")
    return 0


def start():
    old = _read_pid()
    if old and _owned(old):
        print(f"12 MONKEYS MAGIC 8-BALL is already running (PID {old}).")
        return 0

    stray = _find_owned()
    if stray:
        _write_pid(stray[0])
        print(f"12 MONKEYS MAGIC 8-BALL is already running (PID {stray[0]}).")
        return 0

    _remove_pidfile()
    if not APP.is_file():
        raise SystemExit(f"Missing {APP.name}")

    _write_pid(os.getpid())
    os.execv(sys.executable, [sys.executable, str(APP)])


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true")
    group.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    return start() if args.start else stop()


if __name__ == "__main__":
    raise SystemExit(main())
