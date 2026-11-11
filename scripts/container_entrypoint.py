"""Run RAD's API and Streamlit UI in one container web service."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def _add_no_proxy_hosts(*hosts: str) -> None:
    entries: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        for item in os.environ.get(key, "").split(","):
            item = item.strip()
            if item and item.lower() not in {value.lower() for value in entries}:
                entries.append(item)
    for host in hosts:
        if host.lower() not in {value.lower() for value in entries}:
            entries.append(host)
    value = ",".join(entries)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def main() -> int:
    _add_no_proxy_hosts("api.stlouisfed.org", "yahoo.com", "localhost", "127.0.0.1")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        part for part in ("/app/backend", os.environ.get("PYTHONPATH", "")) if part
    )
    os.environ["RAD_API_BASE_URL"] = "http://127.0.0.1:8765"
    port = os.environ.get("PORT", "10000")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("PORT must be a valid TCP port")

    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8765",
    ])
    ui: subprocess.Popen | None = None
    stopping = False
    try:
        for _ in range(60):
            if api.poll() is not None:
                raise RuntimeError("RAD API exited before becoming ready")
            try:
                with urlopen("http://127.0.0.1:8765/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError, OSError):
                time.sleep(1)
        else:
            raise RuntimeError("RAD API did not become ready within 60 seconds")

        ui = subprocess.Popen([
            sys.executable, "-m", "streamlit", "run", "/app/frontend/app.py",
            "--server.address", "0.0.0.0", "--server.port", port,
            "--server.headless", "true",
        ])

        def stop_children(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True
            for process in (ui, api):
                if process is not None and process.poll() is None:
                    process.terminate()

        signal.signal(signal.SIGTERM, stop_children)
        signal.signal(signal.SIGINT, stop_children)
        while True:
            if stopping:
                return 0
            if api.poll() is not None:
                raise RuntimeError("RAD API process stopped unexpectedly")
            if ui.poll() is not None:
                return int(ui.returncode or 0)
            time.sleep(1)
    finally:
        for process in (ui, api):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (ui, api):
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
