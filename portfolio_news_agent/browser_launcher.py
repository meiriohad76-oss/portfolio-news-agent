from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import urlopen


EndpointChecker = Callable[[str], bool]
PopenFactory = Callable[..., subprocess.Popen]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class BrowserLaunchResult:
    status: str
    cdp_url: str
    command: list[str]
    pid: int | None
    ready: bool


def start_debug_browser(
    *,
    profile_dir: str | Path,
    cdp_url: str,
    browser_channel: str | None = "chrome",
    endpoint_checker: EndpointChecker | None = None,
    popen: PopenFactory = subprocess.Popen,
    sleeper: Sleeper = time.sleep,
    wait_seconds: float = 10.0,
    poll_interval: float = 0.5,
) -> BrowserLaunchResult:
    endpoint_checker = endpoint_checker or is_cdp_endpoint_available
    if endpoint_checker(cdp_url):
        return BrowserLaunchResult(
            status="already_running",
            cdp_url=cdp_url,
            command=[],
            pid=None,
            ready=True,
        )

    profile_dir = Path(profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = _port_from_cdp_url(cdp_url)
    executable = _browser_executable(browser_channel or "chrome")
    command = [
        executable,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "--start-maximized",
        "https://seekingalpha.com",
    ]
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = popen(command, **kwargs)
    ready = _wait_for_endpoint(
        cdp_url,
        endpoint_checker=endpoint_checker,
        sleeper=sleeper,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
    )
    return BrowserLaunchResult(
        status="started" if ready else "started_unreachable",
        cdp_url=cdp_url,
        command=command,
        pid=getattr(process, "pid", None),
        ready=ready,
    )


def is_cdp_endpoint_available(cdp_url: str) -> bool:
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=1) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def _wait_for_endpoint(
    cdp_url: str,
    *,
    endpoint_checker: EndpointChecker,
    sleeper: Sleeper,
    wait_seconds: float,
    poll_interval: float,
) -> bool:
    attempts = max(1, int(wait_seconds / poll_interval))
    for attempt in range(attempts):
        if endpoint_checker(cdp_url):
            return True
        if attempt < attempts - 1:
            sleeper(poll_interval)
    return False


def _port_from_cdp_url(cdp_url: str) -> int:
    split = urlsplit(cdp_url)
    if split.port is None:
        raise ValueError(f"CDP URL must include a port: {cdp_url}")
    return int(split.port)


def _browser_executable(browser_channel: str) -> str:
    channel = browser_channel.lower()
    candidates = []
    if channel == "msedge":
        candidates = [
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
        fallback = "msedge"
    else:
        candidates = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        ]
        fallback = "chrome"
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return fallback
