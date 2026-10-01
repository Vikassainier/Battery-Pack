"""Browser smoke test: starts the app, walks through the workflow in headless Chromium, collects JS errors.

Usage:  python scripts/ui_smoke.py [--shots DIR] [--steps cell,pack,...]
Exit code 1 if any console error / page error / failed request is seen.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(ROOT / "output" / "shots"))
    ap.add_argument("--steps", default="cell,pack")
    ap.add_argument("--script", default="", help="python file defining run(page, base) for custom interactions")
    args = ap.parse_args()
    Path(args.shots).mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "battery_thermal.api.main:app", "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        import urllib.request
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        from playwright.sync_api import sync_playwright
        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium"), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url} {r.failure}"))
            page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
            page.goto(base)
            page.wait_for_selector("#nav button.step")
            for step in [s for s in args.steps.split(",") if s]:
                page.click(f"#nav [data-step={step}]")
                page.wait_for_timeout(400)
                page.screenshot(path=str(Path(args.shots) / f"{step}.png"), full_page=True)
            if args.script:
                ns: dict = {}
                exec(Path(args.script).read_text(), ns)
                ns["run"](page, base, args.shots, errors)
            browser.close()
        for e in errors:
            print("ERROR", e)
        print("UI smoke:", "FAILED" if errors else "OK")
        return 1 if errors else 0
    finally:
        srv.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
