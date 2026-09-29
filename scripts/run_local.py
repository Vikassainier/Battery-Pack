#!/usr/bin/env python3
"""Run the tool on your own machine with one command (Windows, macOS, Linux):

    python scripts/run_local.py

The first run creates a virtual environment in ./.venv and installs requirements.txt into it (a minute or two); later runs start
immediately. The server listens on 127.0.0.1 only and the browser opens automatically. Press Ctrl+C to stop.

Options:  --port N   (default 8000; the next free port is used if it is busy)
          --host H   (default 127.0.0.1; use 0.0.0.0 only if other machines should reach it)
          --no-browser   --reload (auto-restart on code changes, for development)   --reinstall (force pip install)
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
STAMP = VENV / ".requirements.stamp"
MIN_PYTHON = (3, 10)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_environment(reinstall: bool = False) -> Path:
    """Create ./.venv and install the runtime requirements if that has not been done for the current requirements.txt."""
    py = venv_python()
    if not py.exists():
        print(f"Creating virtual environment in {VENV} ...")
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
        except Exception as exc:                                     # noqa: BLE001 - e.g. Debian/Ubuntu without python3-venv
            raise SystemExit(f"Could not create a virtual environment ({exc}).\n"
                             "On Debian/Ubuntu install it with:  sudo apt install python3-venv") from exc
    req = ROOT / "requirements.txt"
    if reinstall or not STAMP.exists() or STAMP.stat().st_mtime < req.stat().st_mtime:
        print("Installing requirements (first run only, takes a minute or two) ...")
        subprocess.check_call([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(req)])
        STAMP.write_text("installed\n")
    return py


def port_in_use(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((probe, port)) == 0


def free_port(host: str, start: int, tries: int = 50) -> int:
    for port in range(start, start + tries):
        if not port_in_use(host, port):
            return port
    raise SystemExit(f"No free port between {start} and {start + tries - 1}; use --port to choose another.")


def open_browser_when_ready(url: str, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the EV battery thermal tool locally.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--reinstall", action="store_true")
    args = ap.parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required (this is {sys.version.split()[0]}).")
    py = ensure_environment(args.reinstall)
    port = free_port(args.host, args.port)
    if port != args.port:
        print(f"Port {args.port} is busy - using {port}.")
    url = f"http://127.0.0.1:{port}"
    cmd = [str(py), "-m", "uvicorn", "battery_thermal.api.main:app", "--host", args.host, "--port", str(port)] + (["--reload"] if args.reload else [])
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    print(f"\nEV Battery Thermal Studio  ->  {url}     (Ctrl+C to stop)\n")
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
