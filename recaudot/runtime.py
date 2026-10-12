"""Helpers for starting real local processes in tests and experiments."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"process did not bind port {port} within {timeout_s}s")


def start_service(
    *,
    port: int,
    db_path: str | Path,
    node: str,
    auth_enabled: bool,
) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "recaudot",
        "service",
        "--port",
        str(port),
        "--db",
        str(db_path),
        "--node",
        node,
    ]
    if auth_enabled:
        command.append("--auth")
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_for_port(port)
    return process


def start_gateway(
    *,
    port: int,
    primary_url: str,
    standby_url: str,
    timeout_s: float = 0.25,
    event_log: str | Path | None = None,
) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "recaudot",
        "gateway",
        "--port",
        str(port),
        "--primary",
        primary_url,
        "--standby",
        standby_url,
        "--timeout",
        str(timeout_s),
    ]
    if event_log:
        command.extend(["--event-log", str(event_log)])
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_for_port(port)
    return process


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)

