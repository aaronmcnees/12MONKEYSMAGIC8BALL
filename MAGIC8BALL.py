#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "MAGIC8BALL.html"
STATIC = {
    "/MAGIC8BALL.css": (ROOT / "MAGIC8BALL.css", "text/css; charset=utf-8"),
    "/MAGIC8BALL.js": (ROOT / "MAGIC8BALL.js", "application/javascript; charset=utf-8"),
    "/background.png": (ROOT / "background.png", "image/png"),
    "/title.png": (ROOT / "title.png", "image/png"),
    "/ape.png": (ROOT / "ape.png", "image/png"),
    **{f"/icon{i}.png": (ROOT / f"icon{i}.png", "image/png") for i in range(1, 9)},
}
HOST = "127.0.0.1"
PORT_START = 8790
PORT_END = 8799

lock = threading.Lock()
stop_event = threading.Event()

state = {
    "running": False,
    "source": "",
    "output": "",
    "minimum": None,
    "maximum": None,
    "bytes_total": 0,
    "bytes_done": 0,
    "lines_seen": 0,
    "lines_kept": 0,
    "started": None,
    "ended": None,
    "status": "IDLE",
    "message": "Choose a wordlist.",
    "error": "",
}

def snapshot():
    with lock:
        s = dict(state)
    elapsed = 0.0
    if s["started"]:
        elapsed = (s["ended"] or time.time()) - s["started"]
    s["elapsed"] = elapsed
    s["percent"] = (s["bytes_done"] / s["bytes_total"] * 100.0) if s["bytes_total"] else 0.0
    s["mbps"] = (s["bytes_done"] / 1048576.0 / elapsed) if elapsed > 0 else 0.0
    s["lines_per_sec"] = (s["lines_seen"] / elapsed) if elapsed > 0 else 0.0
    s["lines_cut"] = max(0, s["lines_seen"] - s["lines_kept"])
    s["reduction"] = (s["lines_cut"] / s["lines_seen"] * 100.0) if s["lines_seen"] else 0.0
    return s

def set_state(**kwargs):
    with lock:
        state.update(kwargs)

def json_response(handler, obj, status=200):
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)

def read_json(handler):
    n = int(handler.headers.get("Content-Length", "0") or 0)
    if n > 1024 * 1024:
        raise ValueError("Request too large")
    raw = handler.rfile.read(n) if n else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")

def native_picker(title):
    zenity = shutil.which("zenity")
    if zenity:
        argv = [
            zenity, "--file-selection", f"--title={title}",
            "--file-filter=Wordlists | *.txt *.dict *.lst *.wordlist *"
        ]
        try:
            r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=300)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    kdialog = shutil.which("kdialog")
    if kdialog:
        try:
            r = subprocess.run(
                [kdialog, "--getopenfilename", "", "*.txt *.dict *.lst *.wordlist *|Wordlists"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=300
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
        f"p=filedialog.askopenfilename(title={title!r}, "
        "filetypes=[('Wordlists','*.txt *.dict *.lst *.wordlist'),('All files','*')])\n"
        "print(p)\nroot.destroy()\n"
    )
    try:
        r = subprocess.run([sys.executable, "-c", script],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=300)
        return r.stdout.strip()
    except Exception:
        return ""

def validate_range(minimum, maximum):
    try:
        minimum = int(minimum)
        maximum = int(maximum)
    except Exception:
        raise ValueError("Minimum and maximum must be whole numbers.")
    if minimum < 1:
        raise ValueError("Minimum must be at least 1.")
    if maximum < minimum:
        raise ValueError("Maximum must be greater than or equal to minimum.")
    if maximum > 255:
        raise ValueError("Maximum is capped at 255.")
    return minimum, maximum

def resolve_output(source, output_name):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Source wordlist does not exist.")

    raw = (output_name or "").strip()
    if not raw:
        raise ValueError("Give the new wordlist a name.")

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        output = candidate.resolve()
    else:
        if "/" in raw or "\\" in raw:
            raise ValueError("Enter a filename only; it will be saved beside the source.")
        output = (source.parent / raw).resolve()

    if output == source:
        raise ValueError("Output cannot be the source wordlist.")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {output.name}")
    if not output.parent.is_dir():
        raise ValueError("Output folder does not exist.")
    return source, output

def open_folder(path):
    p = Path(path).expanduser()
    folder = p if p.is_dir() else p.parent
    if not folder.exists():
        raise ValueError("Folder does not exist.")

    for argv in (["xdg-open", str(folder)], ["gio", "open", str(folder)]):
        if shutil.which(argv[0]):
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    raise RuntimeError("No desktop folder opener found.")

def filter_worker(source, output, minimum, maximum):
    temp = output.with_name(f".{output.name}.partial.{os.getpid()}.{int(time.time())}")
    total = source.stat().st_size
    set_state(
        running=True,
        source=str(source),
        output=str(output),
        minimum=minimum,
        maximum=maximum,
        bytes_total=total,
        bytes_done=0,
        lines_seen=0,
        lines_kept=0,
        started=time.time(),
        ended=None,
        status="CRUNCHING",
        message=f"Filtering wordlist. Keeping entries {minimum}–{maximum}.",
        error="",
    )
    stop_event.clear()

    seen = kept = done = 0
    last_push = 0.0

    try:
        with source.open("rb", buffering=1024 * 1024) as src, temp.open("xb", buffering=1024 * 1024) as dst:
            while True:
                if stop_event.is_set():
                    raise InterruptedError("Stopped by user.")

                line = src.readline()
                if not line:
                    break

                seen += 1
                done += len(line)
                payload = line.rstrip(b"\r\n")
                n = len(payload)

                if minimum <= n <= maximum:
                    dst.write(line)
                    kept += 1

                now = time.monotonic()
                if now - last_push >= 0.12:
                    set_state(bytes_done=done, lines_seen=seen, lines_kept=kept)
                    last_push = now

            dst.flush()
            os.fsync(dst.fileno())

        if stop_event.is_set():
            raise InterruptedError("Stopped by user.")

        # Publish atomically without overwriting an existing target.
        os.link(temp, output)
        temp.unlink(missing_ok=True)

        set_state(
            running=False,
            bytes_done=total,
            lines_seen=seen,
            lines_kept=kept,
            ended=time.time(),
            status="DONE",
            message=f"Saved {kept:,} entries to {output.name}",
        )

    except InterruptedError:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        set_state(
            running=False,
            bytes_done=done,
            lines_seen=seen,
            lines_kept=kept,
            ended=time.time(),
            status="STOPPED",
            message="Stopped. Partial output deleted.",
        )
    except Exception as e:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        set_state(
            running=False,
            bytes_done=done,
            lines_seen=seen,
            lines_kept=kept,
            ended=time.time(),
            status="ERROR",
            message="Crunch failed.",
            error=str(e),
        )

def start_filter(cfg):
    if snapshot()["running"]:
        raise RuntimeError("A crunch job is already running.")
    minimum, maximum = validate_range(cfg.get("minimum"), cfg.get("maximum"))
    source, output = resolve_output(cfg.get("source", ""), cfg.get("output", ""))
    threading.Thread(
        target=filter_worker,
        args=(source, output, minimum, maximum),
        daemon=True,
    ).start()
    return {"ok": True, "source": str(source), "output": str(output)}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _serve_file(self, path, content_type):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self._serve_file(HTML, "text/html; charset=utf-8")
            return

        if path in STATIC:
            file_path, content_type = STATIC[path]
            if not file_path.is_file():
                json_response(self, {"error": f"Missing asset: {file_path.name}"}, 404)
                return
            self._serve_file(file_path, content_type)
            return

        if path == "/api/state":
            json_response(self, snapshot())
            return

        if path == "/api/pick":
            json_response(self, {"path": native_picker("Choose source wordlist")})
            return

        json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/start":
                json_response(self, start_filter(read_json(self)))
                return

            if path == "/api/stop":
                stop_event.set()
                json_response(self, {"ok": True})
                return

            if path == "/api/openfolder":
                cfg = read_json(self)
                open_folder(cfg.get("path", ""))
                json_response(self, {"ok": True})
                return

            json_response(self, {"error": "Not found"}, 404)

        except FileExistsError as e:
            json_response(self, {"error": str(e)}, 409)
        except Exception as e:
            json_response(self, {"error": str(e)}, 400)

def find_port():
    for port in range(PORT_START, PORT_END + 1):
        try:
            return ThreadingHTTPServer((HOST, port), Handler), port
        except OSError:
            continue
    raise RuntimeError(f"No free local port between {PORT_START} and {PORT_END}.")

def main():
    if not HTML.is_file():
        raise SystemExit(f"Missing {HTML.name}")
    missing = [path.name for path, _ in STATIC.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing assets: " + ", ".join(sorted(set(missing))))

    server, port = find_port()
    url = f"http://{HOST}:{port}/"
    print(f"12MONKEYS MAGIC8BALL: {url}")
    threading.Timer(0.55, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()

if __name__ == "__main__":
    main()
